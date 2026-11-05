import test from "node:test";
import assert from "node:assert/strict";
import { frameIndex, validManifest, dragPosition, frameLocation, zoomFactor, imageScale } from "../web/viewer.mjs";

test("drag positions clamp or wrap at sequence boundaries", () => {
    assert.equal(frameIndex(-1, 124), 0);
    assert.equal(frameIndex(124, 124), 123);
    assert.equal(frameIndex(-1, 124, true), 123);
    assert.equal(frameIndex(124, 124, true), 0);
    assert.equal(frameIndex(42.4, 124), 42);
});

const grid = { schema_version: 2, status: "complete", type: "output", subfolder: "H3_ORB360/grid_test",
    kind: "orbit_grid", frame_count: 124, fps: 24, width: 682, height: 1023, source_width: 1024, source_height: 1536,
    row_count: 3, elevations: [-30, 0, 30], default_row: 1 };

test('calibrated rigid heights retain three rows and reject invalid floor limits',()=>{
    const rigid={...grid,generation_profile:'fixed3d_camera_grid',pose_status:'fixed_geometry',elevations:[-12,0,30]};
    assert.equal(validManifest(rigid),true);
    for(const elevations of [[0,0,30],[-31,0,30],[-12,1,30],[-12,0,90],[NaN,0,30],[-Infinity,0,30]])
        assert.equal(validManifest({...rigid,elevations}),false);
    assert.equal(validManifest({...grid,elevations:[-12,0,30]}),false);
    assert.equal(validManifest({...rigid,elevations:[-9.254486974537064,0,30]}),true);
});

test('wide-image framing matches saved crop while original comparison stays whole', () => {
    const wide = {...grid, frame_basis_zoom: .5};
    assert.equal(validManifest(wide), true);
    assert.equal(validManifest({...grid,frame_basis_zoom:.1}), false);
    assert.equal(imageScale(wide, .5), 1);
    assert.equal(imageScale(wide, 1), 2);
    assert.equal(imageScale(grid, 1), 1);
    assert.equal(imageScale(wide, .5, true), 1);
});

test("grid manifests distinguish references from full orbits and reject missing rows", () => {
    assert.equal(validManifest(grid), true);
    assert.equal(validManifest({ ...grid, frame_count: 73 }), true);
    assert.equal(validManifest({ ...grid, kind: "references", frame_count: 4, fps: 0 }), true);
    for (const change of [{ row_count: 2 }, { elevations: [0, 30, -30] }, { default_row: 9 }, { frame_count: 4 }, { kind: "fake" }])
        assert.equal(validManifest({ ...grid, ...change }), false);
});

test("dragging changes actual row and frame without wrapping vertical limits", () => {
    const start = { frame: 60, row: 1 };
    assert.deepEqual(dragPosition(start, 0, -100, 200, 200, grid, false), { index: 60, row: 2 });
    assert.deepEqual(dragPosition(start, 0, 300, 200, 200, grid, true), { index: 60, row: 0 });
    assert.deepEqual(dragPosition(start, 200, 0, 200, 200, grid, false), { index: 123, row: 1 });
    assert.deepEqual(dragPosition(start, 200, 0, 200, 200, grid, true), { index: 59, row: 1 });
    const refs = { ...grid, kind: "references", frame_count: 4, fps: 0 };
    assert.deepEqual(dragPosition({ frame: 0, row: 1 }, 200, 0, 200, 200, refs, true), { index: 0, row: 1 });
});

test("frame URLs resolve rows safely and preserve legacy results", () => {
    assert.equal(frameLocation({ ...grid, frame_count: 73 }, 999, 2).filename, "frame_072.png");
    assert.deepEqual(frameLocation(grid, 42, 2), { filename: "frame_042.png", subfolder: "H3_ORB360/grid_test/row_02" });
    assert.deepEqual(frameLocation(grid, "original", 2), { filename: "original.png", subfolder: "H3_ORB360/grid_test" });
    assert.deepEqual(frameLocation(grid, 500, -3), { filename: "frame_123.png", subfolder: "H3_ORB360/grid_test/row_00" });
    assert.equal(frameLocation({ ...grid, schema_version: 1 }, 0).subfolder, "H3_ORB360/grid_test");
    assert.throws(() => frameLocation({ ...grid, subfolder: "../secret" }, 0));
    assert.equal(frameIndex(NaN, 124), 0);
});

test("manifest accepts only completed own-output sequences of the original ratio", () => {
    const m = { schema_version: 1, status: "complete", type: "output", subfolder: "H3_ORB360/20261102_123456_abcd", frame_count: 124, fps: 24, width: 960, height: 640, source_width: 1500, source_height: 1000 };
    assert.equal(validManifest(m), true);
    for (const change of [{ status: "partial" }, { subfolder: "../input" }, { subfolder: "https://example.com" }, { frame_count: 123 }, { width: 961 }])
        assert.equal(validManifest({ ...m, ...change }), false);
});

// User-facing default: horizontal motion wraps; height remains bounded.
test("default drag loops horizontally for all supported sequence lengths", () => {
    for (const count of [73, 124]) {
        const manifest = { ...grid, frame_count: count };
        assert.deepEqual(dragPosition({ frame: count - 1, row: 2 }, 200 / (count - 1), -100, 200, 200, manifest), { index: 0, row: 2 });
        assert.deepEqual(dragPosition({ frame: 0, row: 0 }, -200 / (count - 1), 100, 200, 200, manifest), { index: count - 1, row: 0 });
    }
    const refs = { ...grid, kind: "references", frame_count: 4, fps: 0 };
    assert.deepEqual(dragPosition({ frame: 3, row: 1 }, 50, 0, 200, 200, refs), { index: 0, row: 1 });
});

test("zoom stays finite and within display bounds", () => {
    assert.equal(zoomFactor(1.5), 1.5);
    assert.equal(zoomFactor(-1), 0.5);
    assert.equal(zoomFactor(999), 4);
    for (const v of [undefined, NaN, Infinity]) assert.equal(zoomFactor(v), 1);
});

test("seven heights restore the middle row and remain vertically bounded", () => {
    const seven = { ...grid, row_count: 7, elevations: [-45,-30,-15,0,15,30,45], default_row: 3 };
    assert.equal(validManifest(seven), true);
    assert.equal(frameLocation(seven, 20, 6).subfolder, "H3_ORB360/grid_test/row_06");
    assert.equal(dragPosition({frame:0,row:3},0,-1000,200,200,seven).row,6);
    assert.equal(dragPosition({frame:0,row:3},0,1000,200,200,seven).row,0);
    assert.equal(validManifest({...seven,elevations:[-45,0,45]}),false);
});

test("short vertical drags work despite tall letterboxed nodes", () => {
    assert.equal(dragPosition({frame:17,row:1},0,-40,413,234,grid).row,2);
    assert.equal(dragPosition({frame:17,row:1},0,40,413,234,grid).row,0);
    assert.equal(dragPosition({frame:17,row:1},0,-40,413,900,grid).row,2);
    assert.equal(dragPosition({frame:17,row:1},0,-5,413,234,grid).row,1);
});
