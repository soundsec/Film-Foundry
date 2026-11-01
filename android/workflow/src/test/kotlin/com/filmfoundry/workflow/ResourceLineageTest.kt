package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ResourceLineageTest {
    @Test fun missingMiddleLayerDoesNotBreakExposureLookupAndDeletionNeverCascades() {
        val library=FilmLibrary();library.loadRoll("roll",12,emptyMap());library.commitCameraShot("roll",1,"latent.darkroom_latent.npz");library.seal("roll")
        library.recordDevelopedResult("result","roll",1,"result.darkroom_negative.npz",emptyMap())
        val files=mutableSetOf("latent.darkroom_latent.npz","result.darkroom_negative.npz")
        fun find()=ResourceOrigins.find(library,"roll",1,"result",files::contains)
        assertEquals(ResourceAvailability.AVAILABLE,find().developed)
        files.remove("result.darkroom_negative.npz");assertEquals(ResourceAvailability.MISSING_FILE,find().developed)
        library.removeDevelopedResult("result");assertEquals(ResourceAvailability.REMOVED,find().developed);assertEquals(ResourceAvailability.AVAILABLE,find().exposure)
        library.discardRoll("roll");assertEquals(ResourceAvailability.REMOVED,find().exposure)
    }
}
