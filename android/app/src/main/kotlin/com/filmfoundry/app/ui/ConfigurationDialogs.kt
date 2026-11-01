package com.filmfoundry.app.ui

import android.app.AlertDialog
import android.widget.*
import androidx.activity.ComponentActivity
import com.filmfoundry.interchange.RecipeJson

fun editConfigurationJson(activity: ComponentActivity,shell: MobileShell,title: String,value: Any?,apply: (Any?)->Unit) {
    val input=EditText(activity).apply { setText(RecipeJson.text(value));typeface=android.graphics.Typeface.MONOSPACE;textSize=12f;minLines=8;gravity=android.view.Gravity.TOP }
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(10),shell.dp(20),shell.dp(12)) }
    shell.field(panel,input,title,shell.copy("高级 JSON 定义；应用前校验，取消不会修改草稿。","Advanced JSON definition. Validated before applying; Cancel preserves the draft."))
    val dialog=AlertDialog.Builder(activity).setTitle(title).setView(ScrollView(activity).apply { addView(panel) }).setNegativeButton(shell.copy("取消","Cancel"),null)
        .setPositiveButton(shell.copy("应用","Apply"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        runCatching { apply(RecipeJson.parse(input.text.toString())) }.onSuccess { dialog.dismiss() }.onFailure { input.error=it.message }
    } };shell.decorate(panel);dialog.show()
}

fun askConfigurationName(activity: ComponentActivity,shell: MobileShell,title: String,save: (String)->Unit) {
    val input=EditText(activity).apply { setSingleLine(true);hint=shell.copy("配置名称","Configuration name") }
    val panel=LinearLayout(activity).apply { orientation=LinearLayout.VERTICAL;setPadding(shell.dp(20),shell.dp(8),shell.dp(20),shell.dp(12)) }
    shell.field(panel,input,shell.copy("配置名称（1–64字）","Configuration name (1–64 characters)"))
    val dialog=AlertDialog.Builder(activity).setTitle(title).setView(panel).setNegativeButton(shell.copy("取消","Cancel"),null)
        .setPositiveButton(shell.copy("保存","Save"),null).create()
    dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        runCatching { val text=input.text.toString().trim();require(text.isNotEmpty() && text.length <= 64);save(text) }
            .onSuccess { dialog.dismiss() }.onFailure { input.error=it.message }
    } };shell.decorate(panel);dialog.show()
}
