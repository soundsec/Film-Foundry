package com.filmfoundry.app.session

import android.content.Context
import com.filmfoundry.interchange.BuiltinRecipeCatalog
import java.security.SecureRandom

/** App-local entitlement. Neither the identity nor unlock flag enters recipes or media. */
class SeasonalUnlock(private val context: Context) {
    private val prefs=context.getSharedPreferences("seasonal_collection",Context.MODE_PRIVATE)
    private val resources by lazy { context.assets.open("finder_resources.bin").use { FinderResources(it.readBytes()) } }
    private val puzzle by lazy { SeasonalPuzzle(resources.section("collection")) }
    private val extra by lazy { BuiltinRecipeCatalog.read(resources.section("phone_catalog")) }
    fun pairings()=com.filmfoundry.workflow.PhoneCreativePairings.read(resources.section("phone_pairings"))
    val unlocked get()=prefs.getBoolean("unlocked",false)
    val identity: String get() {
        prefs.getString("identity",null)?.takeIf { it.matches(Regex("[0-9A-F]{12}")) }?.let { return it }
        val bytes=ByteArray(6);SecureRandom().nextBytes(bytes)
        return SeasonalPuzzle.hex(bytes).also { check(prefs.edit().putString("identity",it).commit()) { "Could not save installation ID" } }
    }
    fun cipher()=puzzle.cipher(identity)
    fun tryUnlock(text: String): Boolean {
        if(text.length>256 || !puzzle.matches(text)) return false
        identity
        check(prefs.edit().putBoolean("unlocked",true).commit()) { "Could not save collection unlock" }
        return true
    }
    fun catalog(base: BuiltinRecipeCatalog): BuiltinRecipeCatalog {
        if(!unlocked) return base
        return base.copy(materials=base.materials+extra.materials,scanners=base.scanners+extra.scanners,
            dailyMaterialIds=base.dailyMaterialIds?.plus(extra.materials.map { it.id }))
    }
}
