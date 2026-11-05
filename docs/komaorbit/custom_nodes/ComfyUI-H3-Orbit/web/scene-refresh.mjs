// Core preview extensions may wrap our callback and update the model path afterward.
// Refresh after that synchronous chain (and any returned promise) has settled.
export function installSceneRefresh(node, refresh) {
    const executed = node.onExecuted;
    let generation = 0;
    node.onExecuted = function(...args) {
        const current = ++generation;
        const result = executed?.apply(this, args);
        Promise.resolve(result).then(() => {
            if (current === generation) return refresh();
        }).catch(error => console.error('H3 scene refresh', error));
        return result;
    };
}

export function applySceneSize(node, size) {
    if (![size?.width, size?.height].every(v => Number.isInteger(v) && v > 0)) return;
    node.properties.h3_scene_size = { width: size.width, height: size.height };
    for (const name of ["width", "height"]) {
        const widget = node.widgets?.find(w => w.name === name);
        if (widget) { widget.value = size[name]; widget.callback?.(size[name]); }
    }
}
