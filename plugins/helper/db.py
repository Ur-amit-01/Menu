"""
Database layer (MongoDB via motor).

Collections:
  users             -> one doc per user, including any pending force-sub join requests
  files             -> one doc per shareable link: code -> stored message refs
  settings          -> runtime-editable admin settings (see settings.py wrapper)
  pending_deletions -> auto-delete jobs that must survive a bot restart
  feedback          -> maps a relayed message in an admin's DM back to the
                        user it came from, so a swipe-reply can be routed
                        (see plugins/filestore/feedback.py)
  menu_nodes        -> the dynamic nested-menu tree. One doc per node:
                        {_id, parent_id, type: "menu"|"content", label,
                         order, messages: [...]}. See plugins/menu/.
  menu_sessions     -> one doc per user: {_id: user_id, stack: [node_id,...]}
                        tracking where in the tree that user currently is.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import motor.motor_asyncio
from bson import ObjectId
from bson.errors import InvalidId

from config import DB_URL, DB_NAME

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri: str, database_name: str):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.users = self.db.users
        self.files = self.db.files
        self.settings = self.db.settings
        self.pending_deletions = self.db.pending_deletions
        self.feedback = self.db.feedback
        self.menu_nodes = self.db.menu_nodes
        self.menu_sessions = self.db.menu_sessions

    # ================= Users ================= #
    def _new_user(self, user_id: int) -> Dict:
        return {
            "_id": int(user_id),
            "join_date": datetime.utcnow(),
            "last_seen": datetime.utcnow(),
            "join_requests": {},  # {channel_id_str: requested_at}
            "banned": False,
        }

    async def add_user(self, user_id: int) -> bool:
        """Insert the user if they're new, else just bump last_seen.
        Returns True the first time a given user_id is ever seen, so
        callers can fire "new user" notifications without a separate
        is_user_exist() round-trip."""
        if not await self.is_user_exist(user_id):
            await self.users.insert_one(self._new_user(user_id))
            return True
        await self.users.update_one(
            {"_id": int(user_id)}, {"$set": {"last_seen": datetime.utcnow()}}
        )
        return False

    async def is_user_exist(self, user_id: int) -> bool:
        return bool(await self.users.find_one({"_id": int(user_id)}))

    async def total_users_count(self) -> int:
        return await self.users.count_documents({})

    async def get_all_user_ids(self) -> List[int]:
        return [u["_id"] async for u in self.users.find({}, {"_id": 1})]

    async def is_banned(self, user_id: int) -> bool:
        u = await self.users.find_one({"_id": int(user_id)})
        return bool(u and u.get("banned"))

    # ---- join-request tracking (for "request to join" force-sub channels) ---- #
    async def record_join_request(self, user_id: int, channel_id: int):
        await self.users.update_one(
            {"_id": int(user_id)},
            {"$set": {f"join_requests.{channel_id}": datetime.utcnow()}},
            upsert=True,
        )

    async def has_pending_join_request(self, user_id: int, channel_id: int) -> bool:
        u = await self.users.find_one(
            {"_id": int(user_id)}, {f"join_requests.{channel_id}": 1}
        )
        return bool(u and str(channel_id) in {str(k) for k in u.get("join_requests", {})})

    async def clear_join_request(self, user_id: int, channel_id: int):
        await self.users.update_one(
            {"_id": int(user_id)}, {"$unset": {f"join_requests.{channel_id}": ""}}
        )

    # ================= Files / shareable links ================= #
    async def create_file_link(self, code: str, doc: Dict) -> bool:
        doc["_id"] = code
        doc.setdefault("created_at", datetime.utcnow())
        doc.setdefault("views", 0)
        try:
            await self.files.insert_one(doc)
            return True
        except Exception as e:
            logger.error(f"create_file_link failed: {e}")
            return False

    async def get_file_link(self, code: str) -> Optional[Dict]:
        return await self.files.find_one({"_id": code})

    async def delete_file_link(self, code: str) -> bool:
        result = await self.files.delete_one({"_id": code})
        return result.deleted_count > 0

    async def increment_views(self, code: str):
        await self.files.update_one({"_id": code}, {"$inc": {"views": 1}})

    async def total_links_count(self) -> int:
        return await self.files.count_documents({})

    async def total_files_stored(self) -> int:
        pipeline = [{"$project": {"n": {"$size": "$messages"}}},
                    {"$group": {"_id": None, "total": {"$sum": "$n"}}}]
        agg = await self.files.aggregate(pipeline).to_list(1)
        return agg[0]["total"] if agg else 0

    # ================= Settings (raw KV store) ================= #
    async def save_setting(self, key: str, value):
        await self.settings.update_one(
            {"_id": key},
            {"$set": {"value": value, "updated_at": datetime.utcnow()}},
            upsert=True,
        )

    async def get_setting(self, key: str, default=None):
        doc = await self.settings.find_one({"_id": key})
        return doc["value"] if doc else default

    async def get_all_settings(self) -> Dict:
        return {doc["_id"]: doc["value"] async for doc in self.settings.find({})}

    # ================= Pending auto-deletions ================= #
    # BUGFIX: add_pending_deletion() used to return str(result.inserted_id),
    # while the document's real "_id" field in Mongo is still an ObjectId.
    # remove_pending_deletion() then did delete_one({"_id": <that string>}),
    # which never matches an ObjectId — so completed auto-delete jobs were
    # NEVER cleaned up from pending_deletions. On every restart,
    # restore_pending_deletions() would reload every one of those stale
    # "completed" jobs (their delete_at already in the past, so they'd try
    # to re-delete messages that were already gone) alongside genuinely
    # pending ones, and the collection would grow forever. We now keep the
    # id as a real ObjectId end-to-end, and remove_pending_deletion()
    # tolerates either an ObjectId or a string that looks like one.
    async def add_pending_deletion(self, doc: Dict) -> ObjectId:
        result = await self.pending_deletions.insert_one(doc)
        return result.inserted_id

    async def get_all_pending_deletions(self) -> List[Dict]:
        return [d async for d in self.pending_deletions.find({})]

    async def remove_pending_deletion(self, _id):
        if not isinstance(_id, ObjectId):
            try:
                _id = ObjectId(str(_id))
            except (InvalidId, TypeError):
                logger.warning(f"remove_pending_deletion got a non-ObjectId id: {_id!r}")
                return
        await self.pending_deletions.delete_one({"_id": _id})

    # ================= Feedback relay (user <-> admin DM) ================= #
    # _id is "{admin_chat_id}:{relayed_message_id}" — every message copied
    # into an admin's DM (the content itself and its little info-header)
    # gets its own mapping entry, so an admin can swipe-reply to either one.
    async def save_feedback_message(self, admin_chat_id: int, message_id: int, user_id: int):
        await self.feedback.update_one(
            {"_id": f"{admin_chat_id}:{message_id}"},
            {"$set": {"user_id": int(user_id), "created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def get_feedback_user(self, admin_chat_id: int, message_id: int) -> Optional[int]:
        doc = await self.feedback.find_one({"_id": f"{admin_chat_id}:{message_id}"})
        return doc["user_id"] if doc else None

    # ================= Dynamic nested menu tree ================= #
    # A node's _id is a Mongo ObjectId. parent_id is None only for the
    # single root node (auto-created the first time it's asked for).
    # type "menu" nodes have children; type "content" nodes carry a
    # `messages` list in the same shape as a files-collection doc's
    # `messages` (see save_link / _finalize_entries in upload.py) and are
    # delivered the same way a file-store link is delivered.

    @staticmethod
    def _oid(node_id) -> Optional[ObjectId]:
        """Best-effort str/ObjectId -> ObjectId. None stays None (root)."""
        if node_id is None:
            return None
        if isinstance(node_id, ObjectId):
            return node_id
        try:
            return ObjectId(str(node_id))
        except (InvalidId, TypeError):
            return None

    async def get_menu_root(self) -> Dict:
        root = await self.menu_nodes.find_one({"parent_id": None})
        if root:
            return root
        doc = {
            "parent_id": None,
            "type": "menu",
            "label": "Main Menu",
            "order": 0,
            "messages": [],
            "created_at": datetime.utcnow(),
        }
        result = await self.menu_nodes.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def get_menu_node(self, node_id) -> Optional[Dict]:
        oid = self._oid(node_id)
        if oid is None:
            return None
        return await self.menu_nodes.find_one({"_id": oid})

    async def get_menu_children(self, parent_id) -> List[Dict]:
        cursor = self.menu_nodes.find({"parent_id": self._oid(parent_id)}).sort("order", 1)
        return [doc async for doc in cursor]

    async def menu_label_taken(self, parent_id, label: str, exclude_id=None) -> bool:
        """True if a *sibling* under parent_id already has this exact label
        (case-insensitive) — the only scope duplicates are disallowed in,
        since navigation only ever matches within one node's children."""
        query = {
            "parent_id": self._oid(parent_id),
            "label": {"$regex": f"^{re.escape(label)}$", "$options": "i"},
        }
        exclude = self._oid(exclude_id)
        if exclude is not None:
            query["_id"] = {"$ne": exclude}
        return bool(await self.menu_nodes.find_one(query))

    async def create_menu_node(self, parent_id, label: str, node_type: str,
                                messages: Optional[List[Dict]] = None) -> ObjectId:
        siblings = await self.get_menu_children(parent_id)
        doc = {
            "parent_id": self._oid(parent_id),
            "type": node_type,  # "menu" | "content"
            "label": label,
            "order": len(siblings),
            "messages": messages or [],
            "created_at": datetime.utcnow(),
        }
        result = await self.menu_nodes.insert_one(doc)
        return result.inserted_id

    async def rename_menu_node(self, node_id, label: str):
        await self.menu_nodes.update_one({"_id": self._oid(node_id)}, {"$set": {"label": label}})

    async def set_menu_node_messages(self, node_id, messages: List[Dict]):
        """Full replace of a content node's stored file(s) — no version history kept."""
        await self.menu_nodes.update_one(
            {"_id": self._oid(node_id)}, {"$set": {"messages": messages}}
        )

    async def delete_menu_node_recursive(self, node_id) -> int:
        """Deletes a node and, if it's a menu, every descendant under it.
        Does NOT delete the backed-up messages from BACKUP_CHANNEL (mirrors
        how the rest of the tree keeps working even if you prune a branch;
        call site can choose to also clean up the backup channel).
        Returns the total number of nodes removed."""
        oid = self._oid(node_id)
        if oid is None:
            return 0
        to_delete = [oid]
        frontier = [oid]
        while frontier:
            children = await self.menu_nodes.find({"parent_id": {"$in": frontier}}, {"_id": 1}).to_list(None)
            child_ids = [c["_id"] for c in children]
            if not child_ids:
                break
            to_delete.extend(child_ids)
            frontier = child_ids
        result = await self.menu_nodes.delete_many({"_id": {"$in": to_delete}})
        return result.deleted_count

    async def reorder_menu_node(self, node_id, direction: int) -> bool:
        """direction: -1 to move up, +1 to move down among siblings.
        Returns True if a swap happened."""
        node = await self.get_menu_node(node_id)
        if not node:
            return False
        siblings = await self.get_menu_children(node["parent_id"])
        idx = next((i for i, s in enumerate(siblings) if s["_id"] == node["_id"]), None)
        if idx is None:
            return False
        swap_idx = idx + direction
        if swap_idx < 0 or swap_idx >= len(siblings):
            return False
        other = siblings[swap_idx]
        await self.menu_nodes.update_one({"_id": node["_id"]}, {"$set": {"order": other["order"]}})
        await self.menu_nodes.update_one({"_id": other["_id"]}, {"$set": {"order": node["order"]}})
        return True

    # ---- per-user "where am I in the tree" session (reply-keyboard nav) ---- #
    async def get_menu_session(self, user_id: int, root_id) -> List[str]:
        doc = await self.menu_sessions.find_one({"_id": int(user_id)})
        if doc and doc.get("stack"):
            return doc["stack"]
        stack = [str(root_id)]
        await self.menu_sessions.update_one(
            {"_id": int(user_id)}, {"$set": {"stack": stack}}, upsert=True
        )
        return stack

    async def set_menu_session(self, user_id: int, stack: List[str]):
        await self.menu_sessions.update_one(
            {"_id": int(user_id)}, {"$set": {"stack": stack}}, upsert=True
        )


# Single shared instance used by every plugin.
db = Database(DB_URL, DB_NAME)
