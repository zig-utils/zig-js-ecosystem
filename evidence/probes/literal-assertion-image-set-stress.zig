const std = @import("std");
const regex = @import("regex");
test "literal assertion image-set adversarial input" {
    const a = std.testing.allocator;
    var re = try regex.Regex.compileWithFlags(a, "(?<=image-set\\()((?:[\\w-]{1,256}\\([^)]*\\)|[^)])*)(?=\\))", .{ .ecmascript = true });
    defer re.deinit();
    try std.testing.expect(re.engine_type == .thompson_nfa);
    const input = try a.alloc(u8, 16395);
    defer a.free(input);
    @memcpy(input[0..10], "image-set(");
    @memset(input[10..], 'a');
    try std.testing.expect((try re.find(input)) == null);
    input[input.len - 1] = ')';
    var m = (try re.find(input)).?;
    defer m.deinit(a);
    try std.testing.expectEqual(@as(usize, 10), m.start);
    try std.testing.expectEqual(input.len - 1, m.end);
    try std.testing.expectEqualStrings(input[10 .. input.len - 1], m.captures[0]);
}
