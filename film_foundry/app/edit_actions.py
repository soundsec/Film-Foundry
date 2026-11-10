"""Frontend-neutral actions and bounded in-memory document history.

Action IDs are the future shortcut binding interface; no key map or GUI objects.
Snapshots are owned by the caller (immutable media may share pixel arrays).
"""
from dataclasses import dataclass


@dataclass
class EditAction:
    execute: object
    enabled: object


class ActionRegistry:
    def __init__(self):self.actions={}
    def register(self,name,execute,enabled=lambda:True):self.actions[name]=EditAction(execute,enabled)
    def available(self,name):return name in self.actions and bool(self.actions[name].enabled())
    def invoke(self,name):
        if not self.available(name):return False
        self.actions[name].execute();return True
    def descriptors(self):return tuple({'id':name,'enabled':self.available(name)} for name in self.actions)


class SnapshotHistory:
    def __init__(self,initial,*,limit=30):
        self.items=[initial];self.position=0;self.limit=max(2,limit)
    @property
    def can_undo(self):return self.position>0
    @property
    def can_redo(self):return self.position<len(self.items)-1
    def push(self,snapshot):
        del self.items[self.position+1:]
        self.items.append(snapshot)
        if len(self.items)>self.limit:del self.items[:len(self.items)-self.limit]
        self.position=len(self.items)-1
    def undo(self):
        if self.can_undo:self.position-=1
        return self.items[self.position]
    def redo(self):
        if self.can_redo:self.position+=1
        return self.items[self.position]
