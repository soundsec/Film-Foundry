package com.filmfoundry.workflow

import com.filmfoundry.interchange.DevelopmentTask

interface DevelopmentTaskStore {
    fun latest(rollId: String): DevelopmentTask?
    fun read(rollId: String,taskId: String): DevelopmentTask
    fun create(task: DevelopmentTask)
    fun save(task: DevelopmentTask)
}
