package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.workflow.RecipeCatalogStore

class PrivateRecipeCatalogStore(
    private val storage: StorageAdapter,
    private val relative: String = DataKernel.PHONE_RECIPE_CATALOG_FILENAME,
) : RecipeCatalogStore {
    override fun writeCatalog(bytes: ByteArray) {
        storage.writePrivate(relative, bytes)
    }

    override fun readCatalog(): ByteArray? {
        if (!storage.existsPrivate(relative)) {
            return null
        }
        return storage.readPrivate(relative)
    }
}
