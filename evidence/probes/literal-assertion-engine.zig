const std = @import("std");
const js = @import("js");
pub fn main() !void {
    inline for (.{ @embedFile("literal-assertion-engine-tree.js"), @embedFile("literal-assertion-engine-generator.js") }) |source| {
        const ctx = try js.Context.create(std.heap.page_allocator);
        defer ctx.destroy();
        const value = try ctx.evaluate(source);
        if (!value.isNumber() or value.asNum() != 1) return error.WrongValue;
        std.debug.print("literal assertion production engine probe OK\n", .{});
    }
}
