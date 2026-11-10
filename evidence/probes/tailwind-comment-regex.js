var r = /(?:\s|\/\/.*|\/\*[^]*?\*\/)*/g;
var input = " /*comment*/ //line\n ";
var match = r.exec(input);
if (match[0] !== input || r.lastIndex !== input.length) throw new Error("comment scan mismatch");
console.log("COMMENT REGEX OK");
