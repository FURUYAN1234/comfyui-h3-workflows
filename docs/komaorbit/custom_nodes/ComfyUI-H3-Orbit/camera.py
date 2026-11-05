"""Requested view positions; generated poses are not measured camera calibration."""
AZIMUTHS = (0, 90, 180, 270)
AZIMUTH_WORDS = ("front view", "right side view", "back view", "left side view")
ELEVATIONS = {-45: "low-angle shot", -30: "low-angle shot", -15: "low-angle shot",
              0: "eye-level shot", 15: "elevated shot", 30: "elevated shot", 45: "high-angle shot"}
PROFILES = {"上下7段階": [-45, -30, -15, 0, 15, 30, 45],
            "上下3段階": [-30, 0, 30], "水平のみ（比較用）": [0]}
# Old seven-row manifests remain readable; new generation uses three heights.
GENERATION_PROFILES = ("上下3段階", "水平のみ（比較用）")


def camera_plan(profile, scene_context, frame_basis_zoom=1):
    if profile not in GENERATION_PROFILES:
        raise ValueError("視点構成を選び直してください。")
    plan = {"version": 1, "elevations": list(PROFILES[profile]),
            "azimuths": list(AZIMUTHS), "scene_context": scene_context.strip()}
    if type(frame_basis_zoom) not in (int, float) or frame_basis_zoom not in (.5, 1):
        raise ValueError("広角画像の倍率情報が不正です。")
    if frame_basis_zoom != 1:
        plan['frame_basis_zoom'] = frame_basis_zoom
    return plan


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ValueError("視点計画の形式が一致しません。")
    if plan.get("elevations") not in list(PROFILES.values()) or plan.get("azimuths") != list(AZIMUTHS):
        raise ValueError("視点の段数・順序が一致しません。")
    if not isinstance(plan.get("scene_context"), str):
        raise ValueError("背景説明は文字列で指定してください。")
    if type(plan.get('frame_basis_zoom', 1)) not in (int, float) or plan.get('frame_basis_zoom', 1) not in (.5, 1):
        raise ValueError("広角画像の倍率情報が不正です。")


def anchor_prompts(plan):
    validate_plan(plan)
    instructions = (
        "Render a single full-frame view of the complete scene in the input image from this camera position. "
        "The camera moves around the central subject or group; the subjects remain frozen in their original poses. "
        "Preserve the exact number of people, their identity, hairstyle, clothing, proportions, and accessories. "
        "Retain the original art style, linework, colors, lighting and location. "
        "Keep the scene's background, architecture, ground and distant surroundings as a connected environment. "
        "Extend unseen parts of that same environment naturally to fill every edge of the image. "
        "The visible ground continues beyond the frame; it is not a finite platform or a cut-out diorama. "
        "Keep recognizable landmarks fixed in world space while their perspective changes. "
        "Maintain the subject's apparent size and the viewing distance, with the full original subject or group in frame. "
        "Create one image, not a multi-panel sheet. No blank void, studio replacement, rotated flat card, duplicate person, or new text."
    )
    if plan["scene_context"]:
        instructions += " Additional scene description: " + plan["scene_context"]
    wide = plan.get('frame_basis_zoom', 1) < 1
    if wide:
        instructions += " Keep this wide framing and the small apparent subject size from the input. Preserve the generous surrounding environment. Do not zoom into the subject."
    distance = 'wide shot' if wide else 'medium shot'
    return [f"<sks> {azimuth} {ELEVATIONS[elevation]} {distance}.\n"
            f"Camera elevation: {elevation} degrees relative to the subject center. "
            f"{'Look up from below the subject center' if elevation < 0 else 'Look down from above the subject center' if elevation > 0 else 'View horizontally at subject center height'}. "
            f"Move the camera to that height; keep the subject upright and frozen.\n{instructions}"
            for elevation in plan["elevations"] for azimuth in AZIMUTH_WORDS]


def orbit_prompt(elevation, scene_context):
    if elevation not in ELEVATIONS:
        raise ValueError("この視点構成では指定の高さを使えません。")
    return f"""subject_definitions:
<Picture 1> is the complete scene at the starting azimuth, 0 degrees, at the requested camera elevation of {elevation} degrees relative to the subject center.
<Picture 2> is the same frozen scene viewed from 90 degrees clockwise, at the same elevation and distance.
<Picture 3> is the same frozen scene viewed from 180 degrees clockwise, at the same elevation and distance.
<Picture 4> is the same frozen scene viewed from 270 degrees clockwise, at the same elevation and distance.
<Subject 1> is the central subject or group, with its identity, exact number of people, hairstyle, outfit, proportions, pose and expression shared by all four pictures.
<Subject 2> is the continuous surrounding location, including foreground, ground, architecture and distant background visible across the four pictures.

summary:
[reference generation] ORB360_CW. One uninterrupted clockwise camera orbit around the frozen <Subject 1> inside <Subject 2>. Use all four reference pictures as views of one scene. The camera stays at a constant requested elevation of {elevation} degrees for the entire turn.

retention_analysis:
<Picture 1> ([Shot 1], beginning and end): fully_preserved - starting and returning composition, including the complete background.
<Picture 2> ([Shot 1], first quarter): fully_preserved - right-side view and its surrounding scenery.
<Picture 3> ([Shot 1], halfway): fully_preserved - back view and its surrounding scenery.
<Picture 4> ([Shot 1], third quarter): fully_preserved - left-side view and its surrounding scenery.
<Subject 1> (throughout [Shot 1]): fully_preserved - one stationary subject or group with consistent appearance and pose.
<Subject 2> (throughout [Shot 1]): partially_preserved - retain the landmarks and layout in all references; infer only the connecting unseen surfaces.

detailed_description:
[Shot 1] A full-frame view with the rendering style, linework, texture, color and lighting of the references. Start at <Picture 1>. The camera follows a horizontal circle clockwise around the center of <Subject 1>, maintaining constant elevation, orbit radius, focal length and apparent subject size. The horizon remains level. The subject stays upright and motionless in the world; it does not rotate, tumble, gesture or track the camera. The camera does not rise or fall during this clip.
The four pictures supply the complete surrounding location, not four separate scenes. Keep the buildings, landscape and furniture fixed in world space. Near and far surroundings show their appropriate parallax. Continue visible ground and background beyond every edge, avoiding an isolated floor patch, black void, blank studio, card, turntable, or floating diorama. Preserve source style and subject count, without duplicated faces or limbs. {scene_context}

Use 124 frames at 24 fps, with the last frame at 5.125 seconds. Follow one steady full turn. These quarter-view times are the nearest sample frames:
At 0.000000 seconds (frame 0), starting view corresponding to <Picture 1>.
At 1.291667 seconds (frame 31), approximately 90 degrees clockwise, corresponding to <Picture 2>.
At 2.583333 seconds (frame 62), approximately 180 degrees clockwise, corresponding to <Picture 3>.
At 3.833333 seconds (frame 92), approximately 270 degrees clockwise, corresponding to <Picture 4>.
At 5.125000 seconds (frame 123), 360 degrees, returning to <Picture 1> with the same background and lighting.
Interpolate neighboring views continuously without cuts, direction reversals, zooms or a second revolution. Keep faces, hair, clothing and all objects frozen throughout. Do not erase scenery to conceal the end seam.

overall_soundscape:
N/A

non_diegetic_music:
N/A
"""


def loop_prompt(elevation, scene_context):
    return f"""How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 3.00-second mark of the target video.

integrated_multimodal_description:
[Shot 1] One frozen instant. Only the camera moves. In a continuous 360 orbit. Begin with the complete composition and rendering style of the first image. The last image is the same view. Keep the people, their poses, clothes, hairstyles and expressions stationary. The entire location is a continuous static environment, with buildings and ground fixed in world space. Circle the central subject once, maintaining the starting elevation, viewing distance and focal length, returning to the starting camera position. The supplied image requests an elevation of {elevation} degrees; retain its actual viewing height rather than tilting or tumbling the subject. Natural camera parallax reveals the surrounding location. Fill the complete frame with the connected environment, including unseen areas. Preserve the source art style, linework, colors and lighting. Do not change the scene, add people, turn the subject, rotate a flat card, create a finite floor patch or erase scenery into a blank void. End on the supplied starting image at frame 72, 3.00 seconds. {scene_context}

overall_soundscape:
N/A

non_diegetic_music:
N/A
"""
