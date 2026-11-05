from .nodes import H3OrbitPrepare, H3OrbitSave
from .grid_nodes import H3OrbitPlan, H3OrbitReferences, H3OrbitGridSave
from .backend import H3OrbitBackend
from .snapshot import register_routes
from .rigid_scene import H3OrbitRigidScene, H3OrbitSceneSnapshot
from .wide import H3OrbitWideCanvas, H3OrbitWideComposite
from .rigid_views import H3OrbitRigidViews
from .scene_refine import H3OrbitSceneTexturePrepare,H3OrbitTextureWrap,H3OrbitSceneTextureFinish

from .subject_detail import H3OrbitSubjectDetail

register_routes()

NODE_CLASS_MAPPINGS = {
    "H3OrbitSubjectDetail": H3OrbitSubjectDetail,
    "H3OrbitSceneTexturePrepare":H3OrbitSceneTexturePrepare,"H3OrbitTextureWrap":H3OrbitTextureWrap,"H3OrbitSceneTextureFinish":H3OrbitSceneTextureFinish,
    "H3OrbitPrepare": H3OrbitPrepare, "H3OrbitSave": H3OrbitSave,
    "H3OrbitPlan": H3OrbitPlan, "H3OrbitReferences": H3OrbitReferences,
    "H3OrbitGridSave": H3OrbitGridSave,
    "H3OrbitBackend": H3OrbitBackend,
    "H3OrbitRigidScene": H3OrbitRigidScene,
    "H3OrbitSceneSnapshot": H3OrbitSceneSnapshot,
    "H3OrbitWideCanvas": H3OrbitWideCanvas,
    "H3OrbitWideComposite": H3OrbitWideComposite,
    "H3OrbitRigidViews": H3OrbitRigidViews,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3OrbitSubjectDetail": "H3 Orbit • 顔・服の3Dテクスチャを鮮明化",
    "H3OrbitPrepare": "H3 Orbit • 画像準備（縦横比維持）",
    "H3OrbitSave": "H3 Orbit • 保存・マウス回転",
    "H3OrbitPlan": "H3 Orbit • 背景・上下の視点計画",
    "H3OrbitReferences": "H3 Orbit • 参照画像の保存・再開",
    "H3OrbitGridSave": "H3 Orbit • 保存・上下左右回転",
    "H3OrbitBackend": "H3 Orbit • 生成方式の選択",
    "H3OrbitRigidScene": "H3 Orbit • 人物固定・背景3D再構成",
    "H3OrbitSceneSnapshot": "H3 Orbit • 固定3Dの現在視点を保存",
    "H3OrbitWideCanvas": "H3 Orbit • 画像外の背景を準備",
    "H3OrbitWideComposite": "H3 Orbit • 元絵を保って広角背景を合成",
    "H3OrbitRigidViews": "H3 Orbit • 体形固定・上下左右回転・保存",
}
WEB_DIRECTORY = "./web"
