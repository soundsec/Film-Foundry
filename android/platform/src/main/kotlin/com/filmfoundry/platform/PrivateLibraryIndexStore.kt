package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.workflow.LibraryIndexStore

class PrivateLibraryIndexStore(
    private val storage: StorageAdapter,
    private val relative: String = DataKernel.PHONE_LIBRARY_INDEX_FILENAME,
) : LibraryIndexStore {
    override fun writeIndex(bytes: ByteArray) {
        storage.writePrivate(relative, bytes)
    }

    override fun readIndex(): ByteArray? {
        if (!storage.existsPrivate(relative)) {
            return null
        }
        return storage.readPrivate(relative)
    }
}
