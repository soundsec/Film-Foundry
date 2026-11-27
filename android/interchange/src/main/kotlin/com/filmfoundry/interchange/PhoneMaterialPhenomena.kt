package com.filmfoundry.interchange

/** Private execution selections, independent of shared material fields and bath versions. */
object PhoneMaterialPhenomena {
    const val CURRENT="phone_layer_material_phenomena_v1"
    const val ROLL_SETTING="material_phenomena_revision"
    const val EXPOSURE_OPTION="phone_layer_exposure_model"
    const val DEVELOPMENT_OPTION="phone_silver_formation_model"
    const val INCIDENT_SCOPE = "phone_processing_incident_scope"
    const val FRAME_INCIDENTS = "local_frame_v2"
    const val ROLL_INCIDENTS = "shared_roll_v1"
    val LIGHT_PIPING_EDGES=listOf("none","top","right","bottom","left","long_edges","short_edges","all_edges")
}
