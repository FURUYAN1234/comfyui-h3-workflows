// Preserve subpixel face samples through Spark's source and render accumulator.
// PackedSplats rounds centers to float16; tiny face samples then overlap or leave
// holes. ExtSplats uses float32 centers in BOTH stages. Classic gsplat adds its
// 0.3 px covariance floor without reducing opacity; match that in the preview.
export async function ensureSceneSplatQuality(view, url) {
    const manager=view.getSceneManager(), models=view.getModelManager();
    const model=models.currentModel, old=model?.children?.find(child=>child.isInitialized && child.splats);
    if(!old || !manager.sparkRenderer)throw new Error('人物3Dの描画精度を設定できません。');
    if(!old.extSplats){
        const precise=new old.constructor({url,extSplats:true});
        try {await precise.initialized;}catch(error){precise.dispose();throw error;}
        precise.position.copy(old.position);precise.quaternion.copy(old.quaternion);precise.scale.copy(old.scale);
        precise.visible=old.visible;precise.renderOrder=old.renderOrder;
        model.add(precise);model.remove(old);
        if(models.originalModel===old)models.originalModel=precise;
        old.dispose();
    }
    const previous=manager.sparkRenderer;
    if(!previous.accumExtSplats){
        const renderer=new previous.constructor({renderer:view.renderer,onDirty:previous.onDirty,
            accumExtSplats:true,preBlurAmount:.3,blurAmount:0});
        manager.scene.remove(previous);manager.scene.add(renderer);manager.sparkRenderer=renderer;
        previous.dispose();
    }else{previous.preBlurAmount=.3;previous.blurAmount=0;}
}
