package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.DevelopmentTaskCodec
import com.filmfoundry.kernel.OpticalMaster

/** Validate source ownership before computing. The task stamp describes execution, never optical content. */
internal fun verifyDevelopmentSource(metadata: Map<String,Any?>,record: Map<String,Any?>?) {
    if(record == null) return
    require(record["model"] == DevelopmentTaskCodec.KIND && record["profile"] == DataKernel.PHONE_REDUCED_PROFILE_ID)
    val frame=metadata[DataKernel.FRAME_IDENTITY_KEY] as? Map<*,*>
    require(frame != null && frame["roll_id"] == record["roll_id"] &&
        (frame["frame_index"] as? Number)?.toInt() == (record["frame_index"] as? Number)?.toInt() &&
        (frame["frame_id"] == null || frame["frame_id"] == record["frame_id"]) && metadata["consumed"] != true) {
        "task source does not match frozen camera frame"
    }
}

internal fun stampDevelopment(optical: OpticalMaster,record: Map<String,Any?>?): OpticalMaster {
    if(record == null) return optical
    return OpticalMaster(optical.opticalDensityRgb,optical.metadata+mapOf(DevelopmentTaskCodec.PROVENANCE_KEY to record),
        optical.clearBaseOpticalDensityRgb,optical.spectralComponentAmounts,optical.spectralPostProcessDensityRgb)
}
