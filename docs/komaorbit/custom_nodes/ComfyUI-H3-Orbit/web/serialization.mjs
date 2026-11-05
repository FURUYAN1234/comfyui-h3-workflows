// ComfyUI clones serialized workflows before opening a new file tab. Some
// frontend node definitions contain reactive arrays (notably combo outputs).
// Preserve their JSON data while detaching values structuredClone cannot copy.
export function normalizeWorkflowFields(data) {
    const normalized = [];
    for (const key of Object.keys(data)) {
        if (data[key] === undefined) continue;
        try { structuredClone(data[key]); }
        catch (error) {
            if (error.name !== "DataCloneError") throw error;
            data[key] = JSON.parse(JSON.stringify(data[key]));
            normalized.push(key);
        }
    }
    return normalized;
}
