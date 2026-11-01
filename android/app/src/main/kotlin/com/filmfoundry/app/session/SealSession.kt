package com.filmfoundry.app.session

import com.filmfoundry.platform.MediumStore
import com.filmfoundry.workflow.*

/** Recover only the roll's known empty slots, then publish the reconciled sealed index once. */
class SealSession(private val library: FilmLibrary, private val store: MediumStore) {
    suspend fun seal(rollId: String, control: WorkflowControl = WorkflowControl()): Roll {
        control.checkCancel()
        return library.sealWithSavedShots(rollId) { roll ->
            val saved = linkedMapOf<Int,String>()
            for(frame in roll.frames) {
                if(frame.state != FrameState.EMPTY) continue
                control.checkCancel()
                val path = cameraLatentPath(rollId,frame.frameIndex)
                val identity = FrameIdentity(rollId,frame.frameId,frame.frameIndex)
                if(store.hasRecoverableCameraLatent(path,identity,control)) saved[frame.frameIndex]=path
            }
            control.checkCancel()
            saved
        }
    }
}
