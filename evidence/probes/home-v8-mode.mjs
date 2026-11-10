import v8 from "node:v8";
console.log(JSON.stringify({startupSnapshot: typeof v8.startupSnapshot, building: v8.startupSnapshot.isBuildingSnapshot()}));
