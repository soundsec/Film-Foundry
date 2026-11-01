package com.filmfoundry.app.ui

import android.view.View
import android.widget.*

/** Touch and accessibility entry points share one selection state and confirmation action. */
class LibrarySelectionBar<T>(private val shell: MobileShell,private val allowed: ()->Boolean,key: (T)->String,
    private val all: ()->Unit,private val remove: (List<T>)->Unit,
    private val exportSelected: ((List<T>)->Unit)?=null,private val browseAllowed: ()->Boolean=allowed,private val exportAllowed: ()->Boolean=allowed): LinearLayout(shell.root.context) {
    val state=LibrarySelection(key)
    private val count=TextView(context)
    private val dock=LinearLayout(context).apply { orientation=VERTICAL }
    private val actions=LinearLayout(context)
    private val delete=Button(context).apply { text=shell.copy("删除所选","Delete selected");setOnClickListener { if(allowed() && state.count>0) remove(state.values) } }
    private val export=Button(context).apply { text=shell.copy("导出所选","Export selected");setOnClickListener { if(exportAllowed() && state.count>0) exportSelected?.invoke(state.values) } }
    private val bindings=mutableListOf<()->Unit>()
    init {
        orientation=VERTICAL
        count.textSize=14f;count.setTextColor(if(shell.ivory) 0xFF363028.toInt() else 0xFFF0E9DD.toInt())
        dock.addView(count,LayoutParams(-1,-2))
        fun action(zh: String,en: String,run: ()->Unit)=Button(context).apply {
            text=shell.copy(zh,en);shell.styleButton(this);setOnClickListener { if(allowed()) run() }
        }
        actions.addView(action("全选","Select all",all),LayoutParams(0,-2,1f))
        actions.addView(action("取消","Cancel") { clear() },LayoutParams(0,-2,1f))
        if(exportSelected==null) actions.addView(delete,LayoutParams(0,-2,1f))
        dock.addView(actions)
        if(exportSelected!=null) dock.addView(LinearLayout(context).apply {
            addView(export,LayoutParams(0,-2,1f));addView(delete,LayoutParams(0,-2,1f))
        })
        listOf(delete,export).forEach { shell.styleButton(it) };refresh()
    }
    fun clearBindings() { bindings.clear() }
    fun clear() { state.clear();refresh() }
    fun selectAll(items: List<T>) { state.selectAll(items);refresh() }
    fun bind(view: View,item: T,open: ()->Unit,paint: (Boolean,Boolean)->Unit) {
        val update={ paint(state.active,state.contains(item));view.isSelected=state.contains(item) }
        bindings+=update;update()
        view.accessibilityDelegate=object: View.AccessibilityDelegate() {
            override fun onInitializeAccessibilityNodeInfo(host: View,info: android.view.accessibility.AccessibilityNodeInfo) {
                super.onInitializeAccessibilityNodeInfo(host,info);info.isCheckable=state.active;info.isChecked=state.contains(item)
            }
        }
        view.setOnClickListener { if(state.active) { if(allowed()) { state.toggle(item);refresh() } } else if(browseAllowed()) open() }
        view.setOnLongClickListener { if(allowed()) { state.toggle(item);refresh();view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS) };true }
        view.tooltipText=shell.copy("长按选择","Touch and hold to select")
    }
    fun refresh() {
        visibility=if(state.active) VISIBLE else GONE
        shell.selectionPanel(dock,state.active)
        count.text=if(state.active) shell.copy("已选 ${state.count}","${state.count} selected") else ""
        actions.visibility=if(state.active) VISIBLE else GONE
        delete.isEnabled=allowed() && state.count>0
        export.isEnabled=exportAllowed() && state.count>0
        for(i in 0 until actions.childCount) actions.getChildAt(i).isEnabled=if(actions.getChildAt(i)===delete) allowed() && state.count>0 else allowed()
        bindings.forEach { it() }
    }
}
