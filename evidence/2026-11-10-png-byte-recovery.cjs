const fs=require('node:fs');const zlib=require('node:zlib');const crypto=require('node:crypto');
// Run against the recorded pre-repair checkout: node recovery.cjs FILE SOURCE_DIR OUTPUT_DIR
const root=(process.argv[4]||__dirname)+'/';fs.mkdirSync(root,{recursive:true});
const file=process.argv[2]||'atomic-fx-diagram.png';if(!['atomic-fx-diagram.png','diagram.png'].includes(file))throw new Error('Unexpected recovery input');
const input=fs.readFileSync((process.argv[3]||process.cwd()+'/public/images')+'/'+file);
const table=new Uint32Array(256),inverse=new Uint8Array(256);
for(let i=0;i<256;i++){let c=i;for(let j=0;j<8;j++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;table[i]=c>>>0;inverse[c>>>24]=i;}
const step=(state,byte)=>(table[(state^byte)&255]^(state>>>8))>>>0;
const reverse=(state,byte)=>{const i=inverse[state>>>24];return (((state^table[i])<<8)|(i^byte))>>>0;};
const crc=bytes=>{let c=0xffffffff;for(const b of bytes)c=step(c,b);return (~c)>>>0;};
let offset=8;while(input.toString('ascii',offset+4,offset+8)!=='IDAT')offset+=input.readUInt32BE(offset)+12;
const expectedLength=input.readUInt32BE(offset),start=offset+8,end=input.length-16;
const damaged=input.subarray(start,end),wanted=input.readUInt32BE(end),missing=expectedLength-damaged.length;
if(missing!==1&&missing!==2)throw new Error('Expected one or two missing CR bytes, found '+missing);
const wantedStates=new Uint32Array(damaged.length+1);let current=(~wanted)>>>0;wantedStates[damaged.length]=current;
for(let i=damaged.length-1;i>=0;i--){current=reverse(current,damaged[i]);wantedStates[i]=current;}
let prefix=0xffffffff;for(const b of Buffer.from('IDAT'))prefix=step(prefix,b);
const prefixes=new Uint32Array(damaged.length+1);prefixes[0]=prefix;for(let i=0;i<damaged.length;i++)prefixes[i+1]=step(prefixes[i],damaged[i]);
const positions=[];for(let i=0;i<damaged.length;i++)if(damaged[i]===10)positions.push(i);
const matches=[];let attempts=0;
for(const a of positions){let state=step(prefixes[a],13);if(missing===1){attempts++;if(state===wantedStates[a])matches.push([a]);continue;}for(let b=a;b<damaged.length;b++){
 if(b>a&&damaged[b]===10){attempts++;if(step(state,13)===wantedStates[b])matches.push([a,b]);}
 state=step(state,damaged[b]);
}}
if(matches.length!==1)throw new Error('Expected one CRC-constrained recovery, found '+matches.length);
const [a,b]=matches[0];const repaired=missing===1?Buffer.concat([input.subarray(0,start+a),Buffer.from([13]),input.subarray(start+a)]):Buffer.concat([input.subarray(0,start+a),Buffer.from([13]),damaged.subarray(a,b),Buffer.from([13]),input.subarray(start+b)]);
if(crc(repaired.subarray(offset+4,offset+8+expectedLength))!==wanted)throw new Error('Recovered IDAT CRC disagrees');
let chunks=[];let idat=[];let p=8;
while(p<repaired.length){const length=repaired.readUInt32BE(p),type=repaired.toString('ascii',p+4,p+8),data=repaired.subarray(p+8,p+8+length);if(p+12+length>repaired.length)throw new Error('Truncated chunk');if(crc(repaired.subarray(p+4,p+8+length))!==repaired.readUInt32BE(p+8+length))throw new Error('CRC failure');chunks.push(type);if(type==='IDAT')idat.push(data);p+=length+12;}
if(p!==repaired.length||chunks.at(-1)!=='IEND')throw new Error('Incorrect PNG termination');
const raw=zlib.inflateSync(Buffer.concat(idat));const width=repaired.readUInt32BE(16),height=repaired.readUInt32BE(20);const depth=repaired[24],color=repaired[25],channels={0:1,2:3,3:1,4:2,6:4}[color];if(!channels||repaired[28]!==0||raw.length!==(Math.ceil(width*channels*depth/8)+1)*height)throw new Error('Unexpected PNG scanline count');
fs.writeFileSync(root+file.replace('.png','-recovered.png'),repaired);
const proof={source:'ec12d68abea59b1de25592e59bb02cd915557209',damagedBytes:input.length,recoveredBytes:repaired.length,originalAdvertisedIDATLength:expectedLength,missingBytes:missing,newlinePositions:positions.length,candidatePairs:attempts,uniqueCRCMatchingPair:matches[0],file,absoluteOffsets:matches[0].map((position,index)=>start+position+index),preservedIDATCRC:wanted.toString(16),chunks,decodedScanlineBytes:raw.length,width,height,damagedSHA256:crypto.createHash('sha256').update(input).digest('hex'),recoveredSHA256:crypto.createHash('sha256').update(repaired).digest('hex'),rawScanlineSHA256:crypto.createHash('sha256').update(raw).digest('hex')};fs.writeFileSync(root+file.replace('.png','-recovery-proof.json'),JSON.stringify(proof,null,2)+'\n');console.log(JSON.stringify(proof));
