package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import com.filmfoundry.workflow.DevelopmentTaskStore

/** Index stores IDs only. Per-task checkpoints never rewrite older task recipes or scan directories. */
class PrivateDevelopmentTaskStore(private val storage: StorageAdapter): DevelopmentTaskStore {
    private fun base(rollId: String): String {
        require(rollId.isNotBlank() && rollId !in listOf(".","..") && rollId.none { it in "/\\:" })
        return "development_tasks/$rollId"
    }
    private fun path(rollId: String,taskId: String): String {
        require(taskId.matches(Regex("[A-Za-z0-9_-]{1,80}")))
        return "${base(rollId)}/$taskId.json"
    }
    private fun ids(rollId: String): List<String> {
        val path="${base(rollId)}/index.json"
        return if(storage.existsPrivate(path)) DevelopmentTaskCodec.decodeIds(readBytes(path,512*1024)) else emptyList()
    }
    private fun readBytes(path: String,limit: Int): ByteArray = storage.readPrivate(path) { input ->
        val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
        while(true) { val n=input.read(buffer);if(n < 0) break;require(output.size()+n <= limit);output.write(buffer,0,n) }
        output.toByteArray()
    }
    override fun latest(rollId: String)=ids(rollId).lastOrNull()?.let { read(rollId,it) }
    override fun read(rollId: String,taskId: String)=DevelopmentTaskCodec.decode(readBytes(path(rollId,taskId),1024*1024)).also {
        require(it.id == taskId && it.rollId == rollId) { "development task identity mismatch" }
    }
    override fun create(task: DevelopmentTask) {
        val existing=ids(task.rollId);val path=path(task.rollId,task.id)
        require(task.id !in existing && !storage.existsPrivate(path)) { "task already exists" }
        val bytes=DevelopmentTaskCodec.encode(task);val index=DevelopmentTaskCodec.encodeIds(existing+task.id)
        storage.writePrivate(path,bytes)
        storage.writePrivate("${base(task.rollId)}/index.json",index)
    }
    override fun save(task: DevelopmentTask) {
        require(task.id in ids(task.rollId)) { "task is not catalogued" }
        val previous=read(task.rollId,task.id)
        require(previous.copy(status=task.status,error=task.error,frames=task.frames) == task) { "frozen task changed" }
        require(previous.frames.map { it.copy(phase="pending",formationMs=0,developmentMs=0) } ==
            task.frames.map { it.copy(phase="pending",formationMs=0,developmentMs=0) }) { "task source list changed" }
        storage.writePrivate(path(task.rollId,task.id),DevelopmentTaskCodec.encode(task))
    }
}
