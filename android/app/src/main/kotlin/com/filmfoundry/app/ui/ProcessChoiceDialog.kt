package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity

/** A catalog view: filtering never changes the selected recipe until a row is tapped. */
class ProcessChoiceDialog(private val activity: ComponentActivity,private val shell: MobileShell) {
    data class Entry(val index: Int,val label: String,val group: Int)
    fun show(entries: List<Entry>,selected: Int,choose: (Int)->Unit) {
        val root=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(16),shell.dp(8),shell.dp(16),shell.dp(8)) }
        val tabs=LinearLayout(activity);root.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled=false;addView(tabs) })
        val rows=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL }
        val scroll=ScrollView(activity).apply { addView(rows) };root.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        val buttons=mutableListOf<Button>()
        val dialog=AlertDialog.Builder(activity).setTitle(shell.copy("选择冲洗方案","Choose a process"))
            .setView(root).setNegativeButton(shell.copy("取消","Cancel"),null).create()
        fun group(index: Int) {
            buttons.forEachIndexed { i,b->b.isSelected=i==index };rows.removeAllViews()
            val items=entries.filter { it.group==index }
            if(items.isEmpty()) shell.note(rows,"暂无保存的配方，可在设置中创建。","No saved recipes. Create one in Settings.")
            items.forEach { entry -> shell.action(rows,entry.label,entry.label) { dialog.dismiss();choose(entry.index) }.apply {
                isSelected=entry.index==selected;textAlignment=android.view.View.TEXT_ALIGNMENT_VIEW_START
                if(isSelected) text=entry.label+shell.copy(" · 当前"," · Selected")
            } }
            scroll.scrollTo(0,0);shell.decorate(rows)
        }
        listOf("推荐" to "Recommended","工艺" to "Processes","药液风格" to "Bath styles","我的配方" to "My recipes").forEachIndexed { i,label ->
            buttons+=shell.editorTab(tabs,label.first,label.second) { group(i) }
        }
        group(entries.firstOrNull { it.index==selected }?.group ?: 0)
        dialog.show();dialog.window?.setLayout(-1,(activity.resources.displayMetrics.heightPixels*.72).toInt());shell.decorate(root)
    }
}
