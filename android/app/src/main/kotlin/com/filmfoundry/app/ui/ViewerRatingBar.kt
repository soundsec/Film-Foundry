package com.filmfoundry.app.ui

import android.graphics.Color
import android.widget.*

class ViewerRatingBar(private val shell: MobileShell,private val preferences: GalleryPreferences,
    private val changed: ()->Unit): LinearLayout(shell.root.context) {
    private var key: String?=null
    private val stars=(1..5).map { value -> icon { key?.let { preferences.rate(it,if(preferences.rating(it)==value) 0 else value);paint();changed() } } }
    private val heart=icon { key?.let { preferences.favor(it,!preferences.favorite(it));paint();changed() } }
    init {
        gravity=android.view.Gravity.CENTER;setPadding(shell.dp(8),shell.dp(6),shell.dp(8),shell.dp(6))
        heart.layoutParams=LayoutParams(shell.dp(48),shell.dp(56)).apply { marginStart=shell.dp(16) }
        paint()
    }
    private fun icon(action: ()->Unit)=ImageButton(context).apply {
        setBackgroundColor(Color.TRANSPARENT);setPadding(shell.dp(8),shell.dp(12),shell.dp(8),shell.dp(12));setOnClickListener { action() }
        addView(this,LayoutParams(0,shell.dp(56),1f))
    }
    fun bind(value: String?,enabled: Boolean) { key=value;stars.forEach { it.isEnabled=enabled && value!=null };heart.isEnabled=enabled && value!=null;paint() }
    private fun paint() {
        val rating=key?.let(preferences::rating) ?: 0
        stars.forEachIndexed { index,view ->
            view.setImageDrawable(CatalogGlyph("star",index<rating));view.isSelected=index<rating
            view.contentDescription=shell.copy("${index+1}星，当前${rating}星；再次点当前星级清除","${index+1} stars, current $rating; tap current rating to clear")
        }
        val favorite=key?.let(preferences::favorite)==true
        heart.setImageDrawable(CatalogGlyph("heart",favorite,if(favorite) Color.rgb(239,65,80) else Color.WHITE));heart.isSelected=favorite
        heart.contentDescription=shell.copy(if(favorite) "取消收藏" else "收藏",if(favorite) "Remove favorite" else "Favorite")
    }
}
