// Extend only KomaOrbit view instances; native viewers reload splats when copying.
const views = new WeakMap();
const services = new WeakSet();
export function installSceneViewCopy(service) {
    if (services.has(service)) return;
    services.add(service);
    const copy = service.copyLoad3dState;
    service.copyLoad3dState = async function(source, destination) {
        const context = views.get(source);
        if (!context) return copy.call(this, source, destination);
        const camera = structuredClone(source.getCameraState());
        const transform = structuredClone(source.getModelInfo());
        views.get(destination)?.detach(destination);
        const result = await copy.call(this, source, destination);
        destination.applyModelTransform(transform);
        destination.setCameraState(camera);
        await context.attach(destination);
        return result;
    };
}
export function createSceneViewContext(attach) {
    const cleanups = new Map();
    const context = {
        async attach(view) {
            context.detach(view);
            const cleanup = await attach(view);
            const remove=view.remove;
            view.remove=function(){context.detach(view);return remove.call(this);};
            cleanups.set(view,()=>{view.remove=remove;cleanup();}); views.set(view, context);
        },
        detach(view) {
            cleanups.get(view)?.(); cleanups.delete(view); views.delete(view);
        },
        dispose() {
            for (const view of [...cleanups.keys()]) context.detach(view);
        },
    };
    return context;
}
