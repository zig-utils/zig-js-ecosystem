import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const { generateThumbHash } = await import(pathToFileURL(process.argv[2]).href);
const result = await generateThumbHash(process.argv[3]);
console.log(JSON.stringify({file: process.argv[3], hash: Buffer.from(result.hash).toString("hex"), dataUrlSha256: createHash("sha256").update(result.dataUrl).digest("hex")}));
