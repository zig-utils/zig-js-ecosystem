(function*(){
var r = /(?<=image-set\()((?:[\w-]{1,256}\([^)]*\)|[^)])*)(?=\))/gd;
var m = r.exec("image-set(foo(a), bar(b))");
if(!m || m.index!==10 || m[0]!=="foo(a), bar(b)" || m[1]!==m[0] || m.indices[1][0]!==10 || m.indices[1][1]!==24 || r.lastIndex!==24) throw Error("image-set span");
var named=/(?<=pre)(?<body>(?:ab|a)*?b)(?=!)/gd;
m=named.exec("xxpreaab!");
if(!m || m.index!==5 || m.groups.body!=="aab" || m.indices.groups.body[0]!==5 || m.indices.groups.body[1]!==8) throw Error("named span");
if(named.exec("plain")!==null || named.lastIndex!==0) throw Error("global reset");
if("preaa! x prea!".replace(/(?<=pre)a+(?=!)/g,"X")!=="preX! x preX!") throw Error("replace");
if(!/(?<!a)b(?!x)/.test("cb!") || /(?<!a)b(?!x)/.test("ab!")) throw Error("negative predicate");
return 1;
})().next().value;
