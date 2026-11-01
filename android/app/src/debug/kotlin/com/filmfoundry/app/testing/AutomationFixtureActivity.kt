package com.filmfoundry.app.testing

import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Button
import androidx.activity.ComponentActivity
import com.filmfoundry.app.ui.*

/** Debug-only component fixture: never opens the real catalog or camera. Hardware self-test may use the processing lease. */
class AutomationFixtureActivity : ComponentActivity() {
    lateinit var shell: MobileShell
    lateinit var preferences: GalleryPreferences
    lateinit var rating: ViewerRatingBar
    lateinit var selection: LibrarySelectionBar<String>
    lateinit var tile: Button
    var opened = 0
    var annotations = 0
    var allowExport = false

    override fun getSharedPreferences(name: String, mode: Int): SharedPreferences =
        super.getSharedPreferences("automation_fixture_$name", mode)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if(intent.getBooleanExtra("landscape",false)) requestedOrientation=android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        // The benchmark measures active computation; screen-off suspension is a separate test.
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if(android.os.Build.VERSION.SDK_INT>=27) setTurnScreenOn(true)
        else window.addFlags(android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        getSharedPreferences("gallery_presentation", MODE_PRIVATE).edit().clear().commit()
        shell = MobileShell(this, false, "observe")
        preferences = GalleryPreferences(shell)
        rating = ViewerRatingBar(shell, preferences) { annotations++ }
        rating.bind("fixture-a", true)
        selection = LibrarySelectionBar(shell, { true }, { it: String -> it }, {}, {}, {},
            exportAllowed = { allowExport })
        tile = Button(this).apply { text = "自动化测试条目 / Synthetic fixture" }
        selection.bind(tile, "fixture-a", { opened++ }) { _, _ -> }
        shell.root.addView(rating, 0)
        shell.root.addView(tile, 1)
        shell.root.addView(selection, 2)
        setContentView(shell.root)
    }

    override fun onDestroy() {
        getSharedPreferences("gallery_presentation", MODE_PRIVATE).edit().clear().commit()
        super.onDestroy()
    }
}
