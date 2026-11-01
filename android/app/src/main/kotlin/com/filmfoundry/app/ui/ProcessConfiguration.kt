package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.util.UUID

class ProcessConfiguration(private val activity: ComponentActivity,private val shell: MobileShell,storage: StorageAdapter,
    private val current: ()->Map<String,Any?>,private val changed: ()->Unit) {
    var onPreviewRecipe: (Map<String,Any?>)->Unit={}
    private val catalog=ProcessRecipeCatalog(PrivateRecipeCatalogStore(storage,"phone_process_recipes.json"))
    fun open() {
        val own=catalog.list();val visible=PhoneProcessPresets.visible()
        AlertDialog.Builder(activity).setTitle(shell.copy("独立冲洗配方配置","Process configuration"))
            .setItems((listOf(shell.copy("以当前配方新建","New from current recipe"))+visible.map { shell.copy(it.labelZh,it.labelEn)+shell.copy(" · 内置"," · built-in") }+own.map { it.label }).toTypedArray()) { _,which ->
                if(which == 0) {
                    runCatching { val recipe=(current()["develop"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty();edit(recipe,shell.copy("新冲洗配方","New process")) }
                        .onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() }
                } else if(which<=visible.size) {
                    val item=visible[which-1];edit(item.recipe,shell.copy(item.labelZh,item.labelEn))
                } else AlertDialog.Builder(activity).setTitle(own[which-visible.size-1].label).setItems(arrayOf(shell.copy("复制并编辑","Duplicate & edit"),shell.copy("删除此配置…","Delete configuration…"))) { _,action ->
                    val item=own[which-visible.size-1]
                    if(action == 0) edit(item.recipe,item.label) else AlertDialog.Builder(activity).setTitle(shell.copy("删除冲洗预设？","Delete process preset?"))
                        .setMessage(shell.copy("已装卷、原冲洗任务和已有结果保留。","Loaded rolls, frozen tasks and saved results stay."))
                        .setNegativeButton(shell.copy("取消","Cancel"),null).setPositiveButton(shell.copy("删除预设","Delete preset")) { _,_->runCatching { catalog.remove(item.id);changed() }.onFailure { Toast.makeText(activity,it.message,Toast.LENGTH_LONG).show() } }.show()
                }.show()
            }.show()
    }
    private fun edit(source: Map<String,Any?>,label: String) {
        val material=(current()["film"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
        ProcessProgramEditor(activity,shell).apply { onPreviewRecipe=this@ProcessConfiguration.onPreviewRecipe }.open(source,material) { values ->
            askConfigurationName(activity,shell,shell.copy("保存冲洗配方：","Save process definition: ")+label) { name ->
                catalog.save(UUID.randomUUID().toString(),name,values);changed()
            }
        }
    }
}
