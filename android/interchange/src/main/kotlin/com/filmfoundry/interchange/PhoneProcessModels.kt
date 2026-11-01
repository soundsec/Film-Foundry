package com.filmfoundry.interchange

/** Independent phone implementation selection supplied by the application, never a scanner look. */
object PhoneProcessModels {
    const val PREVIOUS="phone_material_bath_interaction_v2"
    const val LEDGER="phone_material_bath_interaction_v3"
    const val CURRENT="phone_material_bath_interaction_v4"
    const val OPTION="phone_material_process_model"
    fun interactive(value: Any?)=value==CURRENT || value==LEDGER || value==PREVIOUS
    fun activationLedger(value: Any?)=value==CURRENT || value==LEDGER
}
