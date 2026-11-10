const re = new RegExp("(?<=image-set\\()((?:[\\w-]{1,256}\\([^)]*\\)|[^)])*)(?=\\))");
for(const input of ["image-set(foo(a), bar(b))","ximage-set(a)","plain"]){const m=re.exec(input);console.log(JSON.stringify(m ? [m.index,m[0],m[1]] : null));}
