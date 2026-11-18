package com.filmfoundry.app.session

import android.content.Context
import com.filmfoundry.workflow.ExperimentalMaterialAccess

/** Local entitlement only. The flag is not written into a roll, recipe, or picture. */
class ExperimentalMaterialUnlock(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("experimental_materials", Context.MODE_PRIVATE)
    val unlocked get() = prefs.getBoolean("unlocked", false)
    fun tryUnlock(text: String): Boolean {
        if (!ExperimentalMaterialAccess.matches(text)) return false
        check(prefs.edit().putBoolean("unlocked", true).commit()) { "Could not save the experimental materials unlock" }
        return true
    }
}
