# --- START OF FILE routes/user.py ---
from aiohttp import web
from ..constants import EXPERIMENTAL_FEATURES
from ..globals import routes, jwt_auth, users_db
from ..utils import user_env
import folder_paths
import os
import shutil

# Root of ComfyUI
COMFY_ROOT = folder_paths.base_path


def get_global_workflows_root() -> str:
    """
    Global/default workflows folder.

    We prefer:
      <COMFY_ROOT>/user/default/workflows
    and fall back to:
      <COMFY_ROOT>/user_data/workflows
    """
    candidates = [
        os.path.join(COMFY_ROOT, "user", "default", "workflows"),
        os.path.join(COMFY_ROOT, "user_data", "workflows"),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    os.makedirs(candidates[0], exist_ok=True)
    return candidates[0]


def _get_caller_admin_info(request):
    """
    Returns (is_admin: bool, username: str|None, groups: list[str])

    Used to guard admin-only actions.
    """
    token = jwt_auth.get_token_from_request(request)
    if not token:
        return False, None, ["guest"]

    try:
        payload = jwt_auth.decode_access_token(token)
        username = payload.get("username")
        _, rec = users_db.get_user(username)
        groups = [g.lower() for g in rec.get("groups", [])] if rec else ["guest"]
        is_admin = bool(rec and (rec.get("admin") or ("admin" in groups)))
        return is_admin, username, groups
    except Exception as e:
        print(f"[MSS-Login] admin check error: {e}")
        return False, None, ["guest"]


# #region agent log
def _debug_log_me(path: str, is_admin: bool, username) -> None:
    try:
        import json
        import time

        _log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".cursor",
            "debug.log",
        )
        os.makedirs(os.path.dirname(_log_path), exist_ok=True)
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "location": "api_me",
                        "message": "handler_called",
                        "data": {
                            "path": path,
                            "is_admin": is_admin,
                            "username": username,
                        },
                        "hypothesisId": "A",
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass


# #endregion


@routes.get("/mss-login/api/me")
async def api_me(request: web.Request) -> web.Response:
    """
    Basic identity info for the frontend.
    """
    from ..globals import users_db

    is_admin, username, groups = _get_caller_admin_info(request)
    _debug_log_me(request.path, is_admin, username)
    if username is None:
        # no / invalid token → guest
        return web.json_response(
            {
                "username": None,
                "role": "guest",
                "groups": ["guest"],
                "is_admin": False,
                "mfa_enabled": False,
                "experimental_features": EXPERIMENTAL_FEATURES,
            }
        )

    # Choose primary role based on groups priority
    role = "guest"
    for candidate in ["admin", "power", "user", "guest"]:
        if candidate in groups:
            role = candidate
            break

    mfa_enabled = (
        users_db.get_mfa_enabled(username)
        if username and username.lower() != "guest"
        else False
    )

    return web.json_response(
        {
            "username": username,
            "role": role,
            "groups": groups,
            "is_admin": is_admin,
            "mfa_enabled": mfa_enabled,
            "experimental_features": EXPERIMENTAL_FEATURES,
        }
    )


# Frontend calls /mss-login/api/me (underscore); ComfyUI may send /api/mss-login/api/me. Register aliases so both match.
routes.get("/mss-login/api/me")(api_me)
routes.get("/api/mss-login/api/me")(api_me)


@routes.post("/mss-login/api/user-env")
async def api_user_env(request: web.Request) -> web.Response:
    """
    Admin-only per-user environment + workflow management.

    Supported actions (JSON body):
      {
        "action": "...",
        "user": "<target username>",
        ... extra fields ...
      }

    Actions:
      - "status"           → inspect per-user env + gallery root flag
      - "list"             → list env files
      - "delete_file"      → delete a single env file
      - "purge"            → purge env root
      - "set_gallery_root" → toggle gallery root user
      - "list_workflows"   → list per-user workflows
      - "promote_workflow" → copy a user's workflow into global defaults
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    action = (data.get("action") or "").strip()
    target_user = (data.get("user") or "").strip()

    if not target_user:
        return web.json_response({"error": "Missing 'user'"}, status=400)

    # Guard: this whole endpoint is for admins
    is_admin, caller, groups = _get_caller_admin_info(request)
    if not is_admin:
        return web.json_response({"error": "Admin privileges required"}, status=403)

    # --- STATUS ----------------------------------------------------
    if action == "status":
        files = user_env.list_user_files(target_user, max_files=200)
        gallery_root_user = user_env.get_gallery_root_user()
        is_root = gallery_root_user == target_user

        msg = f"User '{target_user}' has {len(files)} file(s) under their environment root."
        if is_root:
            msg += " This user is currently configured as the Gallery root."

        return web.json_response(
            {
                "user": target_user,
                "files": files,
                "is_gallery_root": is_root,
                "message": msg,
            }
        )

    # --- LIST FILES -----------------------------------------------
    if action == "list":
        files = user_env.list_user_files(target_user, max_files=2000)
        return web.json_response({"user": target_user, "files": files})

    # --- DELETE SINGLE FILE ---------------------------------------
    if action == "delete_file":
        rel = (data.get("file") or "").strip().replace("\\", "/")
        if not rel:
            return web.json_response({"error": "Missing 'file'"}, status=400)
        if ".." in rel or rel.startswith("/"):
            return web.json_response({"error": "Invalid file path"}, status=400)

        root = user_env.get_user_root(target_user)
        full = os.path.join(root, rel)

        real_root = os.path.realpath(root)
        if not os.path.realpath(full).startswith(real_root + os.sep):
            return web.json_response({"error": "Invalid file path"}, status=400)

        if os.path.exists(full) and os.path.isfile(full):
            try:
                os.remove(full)
                msg = f"Deleted file '{rel}' for user '{target_user}'."
                print(f"[mss-login] {msg}")
                return web.json_response(
                    {"user": target_user, "file": rel, "message": msg}
                )
            except Exception as e:
                print(f"[mss-login] delete_file error: {e}")
                return web.json_response(
                    {"error": f"Failed to delete: {e}"}, status=500
                )

        msg = f"File '{rel}' not found for user '{target_user}'."
        return web.json_response(
            {"user": target_user, "file": rel, "message": msg}, status=404
        )

    # --- PURGE USER ENV ROOT --------------------------------------
    if action == "purge":
        user_env.purge_user_root(target_user)
        msg = f"Purged environment folders for user '{target_user}'."
        print(f"[mss-login] {msg}")
        return web.json_response({"user": target_user, "message": msg})

    # --- SET / CLEAR GALLERY ROOT ---------------------------------
    if action == "set_gallery_root":
        enable = bool(data.get("enable"))
        if enable:
            user_env.set_gallery_root_user(target_user)
            msg = f"Gallery root set to user '{target_user}'."
            is_root = True
        else:
            user_env.set_gallery_root_user(None)
            msg = "Gallery root cleared."
            is_root = False

        print(f"[mss-login] {msg}")
        return web.json_response(
            {"user": target_user, "message": msg, "is_gallery_root": is_root}
        )

    # --- LIST USER WORKFLOWS --------------------------------------
    if action == "list_workflows":
        workflows = user_env.list_user_workflows(target_user)
        return web.json_response(
            {
                "user": target_user,
                "workflows": workflows,
                "count": len(workflows),
            }
        )

    # --- PROMOTE WORKFLOW TO GLOBAL DEFAULTS ----------------------
    if action == "promote_workflow":
        wf_name = (data.get("workflow") or "").strip().replace("\\", "/")
        if not wf_name:
            return web.json_response({"error": "Missing 'workflow'"}, status=400)
        if ".." in wf_name or wf_name.startswith("/"):
            return web.json_response({"error": "Invalid workflow name"}, status=400)

        delete_source = bool(data.get("delete_source"))

        user_wf_dir = user_env.get_user_workflow_dir(target_user)
        src = os.path.join(user_wf_dir, wf_name)

        real_wf_dir = os.path.realpath(user_wf_dir)
        if not os.path.realpath(src).startswith(real_wf_dir + os.sep):
            return web.json_response({"error": "Invalid workflow name"}, status=400)

        if not (os.path.exists(src) and os.path.isfile(src)):
            return web.json_response(
                {
                    "error": f"Workflow '{wf_name}' not found in user folder.",
                    "user": target_user,
                },
                status=404,
            )

        global_root = get_global_workflows_root()
        dst = os.path.join(global_root, wf_name)

        real_global = os.path.realpath(global_root)
        if not os.path.realpath(dst).startswith(real_global + os.sep):
            return web.json_response({"error": "Invalid workflow name"}, status=400)

        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

            if delete_source:
                try:
                    os.remove(src)
                    msg = (
                        f"Workflow '{wf_name}' from user '{target_user}' "
                        f"promoted to global defaults and removed from the user's folder."
                    )
                except Exception as del_err:
                    msg = (
                        f"Workflow '{wf_name}' promoted to global defaults, "
                        f"but failed to delete source: {del_err}"
                    )
                    print(
                        f"[mss-login] promote_workflow delete_source error: {del_err}"
                    )
            else:
                msg = (
                    f"Workflow '{wf_name}' from user '{target_user}' "
                    f"promoted to global defaults ({dst})."
                )

            print(f"[mss-login] {msg}")
            return web.json_response(
                {
                    "user": target_user,
                    "workflow": wf_name,
                    "message": msg,
                    "global_path": dst,
                    "deleted_source": bool(delete_source),
                }
            )
        except Exception as e:
            print(f"[mss-login] promote_workflow error: {e}")
            return web.json_response(
                {"error": f"Failed to promote workflow: {e}"}, status=500
            )

    # --- UNKNOWN ACTION --------------------------------------------
    return web.json_response({"error": f"Unknown action '{action}'"}, status=400)


routes.post("/mss-login/api/user-env")(api_user_env)
routes.post("/api/mss-login/api/user-env")(api_user_env)


@routes.post("/mss-login-gallery/mark-nsfw")
async def mark_nsfw(request: web.Request) -> web.Response:
    """
    Manually mark an image as NSFW or SFW.
    This endpoint allows gallery apps to review and flag images.

    Body (JSON):
        {
            "filename": "image.png",
            "is_nsfw": true,
            "score": 1.0,  # optional, default 1.0
            "label": "manual"  # optional, default "manual"
        }
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    filename = data.get("filename", "").strip()
    if not filename:
        return web.json_response({"error": "Missing 'filename'"}, status=400)

    # Validate filename is safe (no path traversal)
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.json_response({"error": "Invalid filename"}, status=400)

    # Get output directory and construct full path
    output_dir = folder_paths.get_output_directory()
    image_path = os.path.join(output_dir, filename)

    # If file not found at direct path, search recursively in output directory
    if not os.path.exists(image_path):
        found_path = None
        for root, dirs, files in os.walk(output_dir):
            if filename in files:
                found_path = os.path.join(root, filename)
                # Ensure found file is within output directory (security check)
                if os.path.abspath(found_path).startswith(os.path.abspath(output_dir)):
                    image_path = found_path
                    break

        if not os.path.exists(image_path):
            return web.json_response({"error": "File not found"}, status=404)

    # Final security check - ensure file is within output directory
    if not os.path.abspath(image_path).startswith(os.path.abspath(output_dir)):
        return web.json_response({"error": "Invalid file path"}, status=403)

    # Get NSFW flag (default to True if not provided)
    is_nsfw = bool(data.get("is_nsfw", True))
    score = float(data.get("score", 1.0))
    label = str(data.get("label", "manual"))

    # Import and call the API function
    try:
        from ..api import set_image_nsfw_tag

        success = set_image_nsfw_tag(image_path, is_nsfw, score, label)

        if success:
            return web.json_response(
                {
                    "status": "ok",
                    "message": f"Image marked as {'NSFW' if is_nsfw else 'SFW'}",
                    "filename": filename,
                    "is_nsfw": is_nsfw,
                }
            )
        else:
            return web.json_response({"error": "Failed to set NSFW tag"}, status=500)
    except Exception as e:
        print(f"[mss-login] Error in mark-nsfw endpoint: {e}")
        return web.json_response({"error": str(e)}, status=500)


# --- END OF FILE routes/user.py ---
