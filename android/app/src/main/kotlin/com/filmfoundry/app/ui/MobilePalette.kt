package com.filmfoundry.app.ui

import android.graphics.Color
import com.filmfoundry.mobile.R

enum class MobilePalette(val key: String, val zh: String, val en: String, val style: Int,
    background: String, panel: String, input: String, muted: String, border: String,
    button: String, accent: String, selected: String) {
    DARK("dark","黑橙","Black & amber",R.style.Theme_FilmFoundry_Dark,
        "#222426","#1b1d1f","#17191b","#b2ada5","#484a4d","#35383b","#d0ae78","#494234"),
    IVORY("ivory","象牙白","Ivory",R.style.Theme_FilmFoundry_Ivory,
        "#eee8dc","#e4ddcf","#f5f0e6","#655f54","#b8ae9f","#e1d8c8","#765735","#ddd2bd"),
    SLATE("slate","灰蓝","Slate",R.style.Theme_FilmFoundry_Slate,
        "#27292b","#202224","#191b1d","#aeb5bb","#484d51","#363a3d","#a7c0d2","#34444f"),
    SILVER("silver","浅灰","Silver",R.style.Theme_FilmFoundry_Silver,
        "#e6e8e9","#d9dddf","#f7f8f8","#515d65","#b2b9bd","#dfe2e4","#375b73","#c6d6e0");

    val background = Color.parseColor(background)
    val panel = Color.parseColor(panel)
    val input = Color.parseColor(input)
    val muted = Color.parseColor(muted)
    val border = Color.parseColor(border)
    val button = Color.parseColor(button)
    val accent = Color.parseColor(accent)
    val selected = Color.parseColor(selected)
    fun inkOn(color: Int) = if (Color.luminance(color) > 0.17913f) Color.BLACK else Color.WHITE
}
