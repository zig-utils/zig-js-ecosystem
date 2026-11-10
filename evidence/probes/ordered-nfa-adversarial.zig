const std = @import("std");
const regex = @import("regex");
fn witness(n: usize) !void {
    const a = std.testing.allocator;
    const input = try a.alloc(u8, n);
    defer a.free(input);
    @memset(input, 'a');
    var nested = try regex.Regex.compileWithFlags(a, "^(?:a|aa)*?b$", .{ .ecmascript = true });
    defer nested.deinit();
    try std.testing.expect(nested.engine_type == .thompson_nfa);
    try std.testing.expect((try nested.find(input)) == null);
    const blocks = try a.alloc(u8, n * 5);
    defer a.free(blocks);
    for (0..n) |i| @memcpy(blocks[i * 5 ..][0..5], "/*x*/");
    var comments = try regex.Regex.compileWithFlags(a, "^(?:\\s|\\/\\/.*|\\/\\*[^]*?\\*\\/)*!$", .{ .ecmascript = true });
    defer comments.deinit();
    try std.testing.expect(comments.engine_type == .thompson_nfa);
    try std.testing.expect((try comments.find(blocks)) == null);
    std.debug.print("STRESS {d} ambiguous bytes + {d} comment bytes: real anchored rejection OK\n", .{ n, blocks.len });
}
test "ordered NFA adversarial 4096" {
    try witness(4096);
}
test "ordered NFA adversarial 16384" {
    try witness(16384);
}
test "ordered NFA adversarial 32768" {
    try witness(32768);
}
