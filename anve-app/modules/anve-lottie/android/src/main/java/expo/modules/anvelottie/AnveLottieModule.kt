package expo.modules.anvelottie

import android.content.Context
import android.view.ViewGroup
import android.widget.ImageView
import com.airbnb.lottie.LottieAnimationView
import com.airbnb.lottie.LottieDrawable
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.views.ExpoView

/**
 * Plays a Lottie animation (the JSON itself is handed over from JavaScript, already in the
 * app's colours). A thin wrapper around Airbnb's Lottie for Android, kept in the app
 * because the ready-made React Native package adds a C++ build step whose file paths are
 * too long for this project's Windows build.
 */
class AnveLottieModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("AnveLottie")

    View(AnveLottieView::class) {
      Prop("json") { view: AnveLottieView, json: String? -> view.json = json }
      Prop("cacheKey") { view: AnveLottieView, key: String? -> view.cacheKey = key }
      Prop("loop") { view: AnveLottieView, loop: Boolean? -> view.loop = loop ?: true }
      Prop("speed") { view: AnveLottieView, speed: Float? -> view.speed = speed ?: 1f }
      // where the animation ends, 0..1 (some files draw themselves away again at the end)
      Prop("end") { view: AnveLottieView, end: Float? -> view.end = end ?: 1f }
      // no movement: show this moment (0..1) as a still picture
      Prop("still") { view: AnveLottieView, still: Float? -> view.still = still }
      // room round the drawing, in dp: some files draw a little outside their own frame
      Prop("inset") { view: AnveLottieView, inset: Float? -> view.inset = inset ?: 0f }
      OnViewDidUpdateProps { view: AnveLottieView -> view.apply() }
    }
  }
}

class AnveLottieView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  override val shouldUseAndroidLayout = true

  var json: String? = null
  var cacheKey: String? = null
  var loop = true
  var speed = 1f
  var end = 1f
  var still: Float? = null
  var inset = 0f
  private var shown: String? = null

  private val lottie = LottieAnimationView(context).also {
    it.layoutParams = LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
    it.scaleType = ImageView.ScaleType.FIT_CENTER
    it.setClipToCompositionBounds(false)
    addView(it)
  }

  fun apply() {
    val data = json ?: return
    val pad = (inset * resources.displayMetrics.density).toInt()
    if (lottie.paddingLeft != pad) lottie.setPadding(pad, pad, pad, pad)
    val key = cacheKey ?: data.hashCode().toString()
    val fresh = key != shown
    if (fresh) {
      shown = key
      lottie.setAnimationFromJson(data, key)
    }
    lottie.repeatCount = if (loop) LottieDrawable.INFINITE else 0
    lottie.speed = speed
    lottie.setMinAndMaxProgress(0f, end.coerceIn(0.01f, 1f))
    val at = still
    if (at != null) {
      lottie.pauseAnimation()
      lottie.progress = at.coerceIn(0f, 1f)
    } else if (fresh || !lottie.isAnimating) {
      lottie.playAnimation()
    }
  }
}
