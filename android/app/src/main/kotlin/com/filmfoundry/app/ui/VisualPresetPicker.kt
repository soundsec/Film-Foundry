package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.graphics.Bitmap
import android.view.*
import android.widget.*
import androidx.activity.ComponentActivity
import org.json.JSONArray

/** Small display samples only. Visible rows request work; identities, not row positions, receive it. */
class VisualPresetPicker(private val activity: ComponentActivity,private val shell: MobileShell) {
    data class Choice(val id: String,val label: String,val detail: String="")
    fun show(title: String,scope: String,choices: List<Choice>,selectedId: String?,
        samples: (List<Choice>,(String,Bitmap?)->Unit)->(()->Unit),
        selected: (Choice)->Unit,closed: (Boolean,Choice?)->Unit) {
        val prefs=activity.getSharedPreferences("visual_presets",0)
        val favorites=prefs.getStringSet("${scope}_favorites",emptySet()).orEmpty().toMutableSet()
        val recent=runCatching { JSONArray(prefs.getString("${scope}_recent","[]")) }.getOrDefault(JSONArray()).let { a->List(a.length()) { a.getString(it) } }
        val root=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(12),0,shell.dp(12),shell.dp(8)) }
        val filters=LinearLayout(activity);root.addView(filters)
        val list=ListView(activity).apply { dividerHeight=shell.dp(4) };root.addView(list,LinearLayout.LayoutParams(-1,shell.dp(250)))
        val empty=TextView(activity).apply { text=shell.copy("这里还没有预设","No presets here yet");gravity=Gravity.CENTER;setPadding(0,shell.dp(12),0,shell.dp(12)) };root.addView(empty);list.emptyView=empty
        var visible=choices;var chosen=choices.firstOrNull { it.id==selectedId };var active=true;var accepted=false;var generation=0;var filterMode=0
        var cancelSamples: ()->Unit={};var requested=emptySet<String>()
        val images=linkedMapOf<String,Bitmap>();val failed=mutableSetOf<String>()
        fun bindImage(view: ImageView,key: String) {
            val bitmap=images[key]
            if(bitmap!=null) view.setImageBitmap(bitmap) else view.setImageResource(if(key in failed) android.R.drawable.ic_menu_report_image else android.R.drawable.ic_menu_gallery)
            view.contentDescription=if(key in failed) shell.copy("样张暂不可用","Sample unavailable") else choices.firstOrNull { it.id==key }?.label
        }
        fun paintRows() {
            for(i in 0 until list.childCount) {
                val row=list.getChildAt(i) as? LinearLayout ?: continue
                val key=row.tag as? String ?: continue
                bindImage(row.getChildAt(0) as ImageView,key)
                row.isSelected=chosen?.id==key
            }
        }
        val adapter=object: BaseAdapter() {
            override fun getCount()=visible.size
            override fun getItem(position: Int)=visible[position]
            override fun getItemId(position: Int)=position.toLong()
            override fun getView(position: Int,reused: View?,parent: ViewGroup): View {
                val item=visible[position]
                val row=(reused as? LinearLayout) ?: LinearLayout(activity).apply {
                    gravity=Gravity.CENTER_VERTICAL;setPadding(shell.dp(6),shell.dp(6),shell.dp(6),shell.dp(6))
                    addView(ImageView(activity).apply { scaleType=ImageView.ScaleType.FIT_CENTER;setBackgroundColor(0xFF171717.toInt()) },LinearLayout.LayoutParams(shell.dp(88),shell.dp(62)))
                    addView(TextView(activity).apply { textSize=14f;setPadding(shell.dp(10),0,shell.dp(4),0);setTextColor(if(shell.ivory) 0xFF292721.toInt() else 0xFFEFEAE1.toInt()) },LinearLayout.LayoutParams(0,-2,1f))
                    addView(Button(activity).apply { minWidth=0;minimumWidth=0;setPadding(0,0,0,0);isFocusable=false;shell.styleButton(this) },LinearLayout.LayoutParams(shell.dp(48),shell.dp(48)))
                }
                row.tag=item.id;row.setBackgroundColor(if(chosen?.id==item.id) (if(shell.ivory) 0xFFE9E0D1.toInt() else 0xFF393126.toInt()) else android.graphics.Color.TRANSPARENT)
                bindImage(row.getChildAt(0) as ImageView,item.id)
                (row.getChildAt(1) as TextView).text=item.label+(if(item.detail.isBlank()) "" else "\n"+item.detail)
                (row.getChildAt(2) as Button).apply {
                    text=if(item.id in favorites) "★" else "☆";contentDescription=shell.copy("收藏：","Favorite: ")+item.label
                    setOnClickListener {
                        if(!favorites.add(item.id)) favorites.remove(item.id)
                        prefs.edit().putStringSet("${scope}_favorites",favorites.toSet()).apply()
                        text=if(item.id in favorites) "★" else "☆"
                        if(filterMode==1) { visible=choices.filter { it.id in favorites };notifyDataSetChanged() }
                    }
                };return row
            }
        };list.adapter=adapter
        fun requestVisible() {
            if(!active || visible.isEmpty()) return
            val first=list.firstVisiblePosition.coerceAtLeast(0)
            val last=maxOf(first+3,list.lastVisiblePosition+1).coerceAtMost(visible.lastIndex)
            val wanted=visible.subList(first.coerceAtMost(last),last+1).filter { it.id !in images && it.id !in failed }
            val keys=wanted.map { it.id }.toSet()
            if(keys.isEmpty() || keys.all { it in requested }) return
            cancelSamples();requested=keys;val token=++generation
            cancelSamples=samples(wanted) { key,bitmap ->
                if(!active || token!=generation) { bitmap?.recycle();return@samples }
                if(bitmap==null) failed+=key else images.put(key,bitmap)?.takeIf { it!==bitmap }?.recycle()
                val keep=visible.subList(first.coerceAtMost(last),last+1).map { it.id }.toSet()
                while(images.size>32) { val old=images.keys.firstOrNull { it !in keep } ?: break;images.remove(old)?.recycle() }
                paintRows()
            }
        }
        val filterButtons=mutableListOf<Button>()
        listOf("全部" to "All","收藏" to "Favorites","最近" to "Recent").forEachIndexed { index,label ->
            filterButtons+=shell.editorTab(filters,label.first,label.second) {
                filterMode=index
                cancelSamples();generation++;requested=emptySet()
                visible=when(index) { 1->choices.filter { it.id in favorites };2->recent.mapNotNull { id->choices.firstOrNull { it.id==id } };else->choices }
                filterButtons.forEachIndexed { i,b->b.isSelected=i==index };adapter.notifyDataSetChanged();list.post { requestVisible() }
            }.apply { layoutParams=LinearLayout.LayoutParams(0,-2,1f) }
        };filterButtons.first().isSelected=true
        list.setOnItemClickListener { _,_,position,_ -> chosen=visible[position];selected(chosen!!);adapter.notifyDataSetChanged();list.post { requestVisible() } }
        list.setOnScrollListener(object: AbsListView.OnScrollListener {
            override fun onScrollStateChanged(view: AbsListView?,state: Int)=Unit
            override fun onScroll(view: AbsListView?,first: Int,count: Int,total: Int) { list.post { requestVisible() } }
        })
        val dialog=AlertDialog.Builder(activity).setTitle(title).setView(root)
            .setNegativeButton(shell.copy("取消","Cancel"),null).setPositiveButton(shell.copy("完成","Done")) { _,_->accepted=true }.create()
        dialog.setOnDismissListener {
            active=false;generation++;cancelSamples();list.adapter=null
            if(accepted && chosen!=null) prefs.edit().putString("${scope}_recent",JSONArray((listOf(chosen!!.id)+recent).distinct().take(8)).toString()).apply()
            closed(accepted,chosen);images.values.forEach { it.recycle() };images.clear()
        }
        dialog.show();dialog.window?.let { it.setGravity(Gravity.BOTTOM);it.setLayout(-1,-2);it.setDimAmount(.2f) }
        list.post { requestVisible() }
    }
}
