package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.content.Context
import android.widget.*

/** Presentation and catalog annotations; no media or recipe writes. */
class GalleryPreferences(private val shell: MobileShell) {
    private val context=shell.root.context
    private val prefs=context.getSharedPreferences("gallery_presentation",Context.MODE_PRIVATE)
    val columns get()=prefs.getInt("columns",3).takeIf { it in listOf(1,3,5) } ?: 3
    val continuous get()=prefs.getBoolean("continuous",false)
    fun cycleLayout() { prefs.edit().putInt("columns",when(columns) { 3->5;5->1;else->3 }).apply() }
    fun rating(key: String)=prefs.getInt("rating_$key",0).coerceIn(0,5)
    fun favorite(key: String)=prefs.getBoolean("favorite_$key",false)
    fun rate(key: String,value: Int) { prefs.edit().putInt("rating_$key",value.coerceIn(0,5)).apply() }
    fun favor(key: String,value: Boolean) { prefs.edit().putBoolean("favorite_$key",value).apply() }
    fun query()=GalleryQuery(prefs.getString("sort","time") ?: "time",prefs.getBoolean("descending",true),
        prefs.getBoolean("favorites",false),prefs.getString("rating_relation",if(prefs.getInt("minimum",0)>0) "ge" else "any") ?: "any",
        prefs.getInt("rating_value",prefs.getInt("minimum",0)).coerceIn(0,5))
    fun accepts(key: String)=query().accepts(rating(key),favorite(key))
    fun badge(key: String)=listOfNotNull(if(favorite(key)) "♥" else null,if(rating(key)>0) "★${rating(key)}" else null).joinToString(" ")
    fun open(rolls: List<Pair<String,String>>,selectedRoll: String?,changed: (String?)->Unit) {
        val panel=LinearLayout(context).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(8)) }
        val saved=query()
        fun choice(zh: String,en: String,labels: List<String>,selected: Int): Spinner {
            shell.note(panel,zh,en)
            return Spinner(context).apply { adapter=ArrayAdapter(context,android.R.layout.simple_spinner_dropdown_item,labels);setSelection(selected.coerceIn(0,labels.lastIndex));panel.addView(this) }
        }
        val order=choice("排序依据","Sort by",listOf(shell.copy("创建时间","Created time"),shell.copy("文件大小","File size"),shell.copy("卷名","Roll name")),listOf("time","size","name").indexOf(saved.sort))
        val direction=choice("顺序","Direction",listOf(shell.copy("升序","Ascending"),shell.copy("降序","Descending")),if(saved.descending) 1 else 0)
        val roll=choice("胶卷","Roll",listOf(shell.copy("全部胶卷","All rolls"))+rolls.map { it.second },rolls.indexOfFirst { it.first==selectedRoll }+1)
        val favorite=CheckBox(context).apply { text=shell.copy("仅收藏","Favorites only");isChecked=saved.favoriteOnly };panel.addView(favorite)
        val relation=choice("评级条件","Rating comparison",listOf(shell.copy("不限","Any"),">","<","≥","≤"),listOf("any","gt","lt","ge","le").indexOf(saved.ratingRelation))
        val stars=choice("星级（0为未评级）","Stars (0 is unrated)",(0..5).map { it.toString() },saved.rating)
        val navigation=choice("浏览方式","Browsing",listOf(shell.copy("翻页","Pages"),shell.copy("连续滑动","Continuous scrolling")),if(continuous) 1 else 0)
        AlertDialog.Builder(context).setTitle(shell.copy("内容筛选与排序","Filter & sort"))
            .setView(ScrollView(context).apply { addView(panel) }).setNegativeButton(shell.copy("取消","Cancel"),null)
            .setPositiveButton(shell.copy("应用","Apply")) { _,_->
                prefs.edit().putString("sort",listOf("time","size","name")[order.selectedItemPosition]).putBoolean("descending",direction.selectedItemPosition==1)
                    .putBoolean("favorites",favorite.isChecked).putString("rating_relation",listOf("any","gt","lt","ge","le")[relation.selectedItemPosition])
                    .putInt("rating_value",stars.selectedItemPosition).putBoolean("continuous",navigation.selectedItemPosition==1).apply()
                changed(rolls.getOrNull(roll.selectedItemPosition-1)?.first)
            }.show()
    }
}
