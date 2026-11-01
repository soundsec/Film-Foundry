package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.app.session.LibraryFacts
import com.filmfoundry.interchange.RecipeJson
import java.util.concurrent.Executor

/** Fixed summary and section tabs; only the read-only configuration scrolls. */
class LibraryInfoDialog(private val activity: ComponentActivity,private val shell: MobileShell,private val worker: Executor) {
    data class Section(val title: String,val values: Map<String,Any?> = emptyMap(),val description: String?=null)
    data class Content(val facts: LibraryFacts,val sections: List<Section>,val photo: Boolean=false,val material: Boolean=false,val note: String?=null)
    fun show(title: String,load: ()->Content) {
        val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(12),shell.dp(20),shell.dp(8)) }
        val summary=TextView(activity).apply { text=shell.copy("正在读取信息…","Reading information…");textSize=14f };panel.addView(summary)
        val tabs=LinearLayout(activity)
        panel.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(tabs) })
        val text=TextView(activity).apply { textSize=12f;typeface=android.graphics.Typeface.MONOSPACE;setTextIsSelectable(true);setPadding(0,shell.dp(12),0,shell.dp(12)) }
        val scroll=ScrollView(activity).apply { addView(text) };panel.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        val dialog=AlertDialog.Builder(activity).setTitle(title).setView(panel).setNegativeButton(shell.copy("关闭","Close"),null).create()
        dialog.show();dialog.window?.setLayout(-1,(activity.resources.displayMetrics.heightPixels*.8).toInt());shell.decorate(panel)
        worker.execute {
            val result=runCatching { load().let { content -> content to content.sections.map { RecipeJson.text(it.values) } } }
            activity.runOnUiThread {
                if(activity.isDestroyed || !dialog.isShowing) return@runOnUiThread
                result.fold({ (content,formatted) ->
                    summary.text=LibraryInfoPresentation.brief(content.facts,shell.copy("zh","en")=="en",content.photo,content.material)+content.note?.let { "\n$it" }.orEmpty()
                    val buttons=mutableListOf<Button>()
                    fun select(index: Int) {
                        val section=content.sections[index]
                        text.typeface=if(section.description!=null) android.graphics.Typeface.DEFAULT else android.graphics.Typeface.MONOSPACE
                        text.text=section.description ?: if(section.values.isEmpty()) shell.copy("未记录此项配置","This configuration was not recorded") else formatted[index]
                        buttons.forEachIndexed { i,b -> b.isSelected=i==index };scroll.scrollTo(0,0)
                    }
                    content.sections.forEachIndexed { index,section ->
                        buttons+=shell.editorTab(tabs,section.title,section.title) { select(index) }
                    }
                    if(content.sections.isNotEmpty()) select(0)
                    shell.decorate(panel)
                },{ error -> summary.text=shell.copy("信息读取失败，原资源保留","Could not read information; original resource retained");text.text=error.message })
            }
        }
    }
}
