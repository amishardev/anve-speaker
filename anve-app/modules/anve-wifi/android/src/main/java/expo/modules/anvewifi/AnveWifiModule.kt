package expo.modules.anvewifi

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.WindowInsets
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.net.Inet4Address
import java.util.Collections

/**
 * Joining the ANVE box's setup hotspot from the app.
 *
 * The hotspot has no internet. Android would normally refuse to use such a network, or
 * send the app's requests over mobile data instead, and the box (10.42.0.1) would never
 * be reached. So the app asks for that one network by name (Android shows its own
 * "connect to device" sheet) and then BINDS THIS APP'S TRAFFIC to it until leave().
 * The rest of the phone stays on its normal network the whole time.
 */
class AnveWifiModule : Module() {
  private var callback: ConnectivityManager.NetworkCallback? = null
  private val main = Handler(Looper.getMainLooper())

  private val context: Context
    get() = appContext.reactContext ?: throw IllegalStateException("The app is not running")
  private val connectivity: ConnectivityManager
    get() = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
  private val wifi: WifiManager
    get() = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager

  override fun definition() = ModuleDefinition {
    Name("AnveWifi")

    AsyncFunction("scan") { promise: Promise ->
      scan(promise)
    }

    Function("currentSsid") {
      @Suppress("DEPRECATION")
      val ssid = try { wifi.connectionInfo?.ssid ?: "" } catch (e: SecurityException) { "" }
      if (ssid == WifiManager.UNKNOWN_SSID) "" else ssid.trim('"')
    }

    AsyncFunction("join") { ssid: String, password: String, promise: Promise ->
      join(ssid, password, promise)
    }

    Function("leave") {
      release()
    }

    // For someone who joined the box's hotspot in the phone's own Wi-Fi settings: send
    // this app's traffic over Wi-Fi even though that Wi-Fi has no internet (Android would
    // otherwise prefer mobile data, and 10.42.0.1 would not be reached). Undone by leave().
    Function("useWifi") {
      var bound = false
      @Suppress("DEPRECATION")
      for (network in connectivity.allNetworks) {
        val caps = connectivity.getNetworkCapabilities(network) ?: continue
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
          bound = connectivity.bindProcessToNetwork(network)
          break
        }
      }
      bound
    }

    // How much of the screen the status bar (top) and the navigation bar (bottom) cover,
    // in layout units. The app draws edge to edge and keeps its content clear of both.
    Function("insets") {
      val density = context.resources.displayMetrics.density
      val root = appContext.currentActivity?.window?.decorView?.rootWindowInsets
      var top = -1
      var bottom = -1
      if (root != null) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
          val i = root.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
          top = i.top
          bottom = i.bottom
        } else {
          @Suppress("DEPRECATION")
          top = root.systemWindowInsetTop
          @Suppress("DEPRECATION")
          bottom = root.systemWindowInsetBottom
        }
      }
      mapOf("top" to if (top < 0) -1.0 else (top / density).toDouble(), "bottom" to if (bottom < 0) -1.0 else (bottom / density).toDouble())
    }

    AsyncFunction("discover") { timeoutMs: Int, promise: Promise ->
      discover(timeoutMs, promise)
    }

    OnDestroy {
      release()
    }
  }

  private fun results(): List<Map<String, Any>> {
    val best = HashMap<String, Int>()
    @Suppress("DEPRECATION")
    for (r in wifi.scanResults) {
      val name = r.SSID ?: continue
      if (name.isEmpty()) continue
      best[name] = maxOf(best[name] ?: -200, r.level)
    }
    return best.map { mapOf("ssid" to it.key, "level" to it.value) }
  }

  private fun scan(promise: Promise) {
    try {
      if (!wifi.isWifiEnabled) {
        promise.reject("wifi_off", "Wi-Fi is switched off", null)
        return
      }
      var done = false
      val app = context.applicationContext
      val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
          if (done) return
          done = true
          try { app.unregisterReceiver(this) } catch (e: Exception) {}
          try { promise.resolve(results()) } catch (e: SecurityException) {
            promise.reject("permission", "The location permission is needed to see Wi-Fi names", e)
          }
        }
      }
      val filter = IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        app.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
      } else {
        app.registerReceiver(receiver, filter)
      }
      // Android allows an app only a few scans every two minutes. When it refuses, the
      // last results (at most a couple of minutes old) are used.
      @Suppress("DEPRECATION")
      val started = wifi.startScan()
      main.postDelayed({
        if (!done) receiver.onReceive(null, null)
      }, if (started) 9000 else 300)
    } catch (e: SecurityException) {
      promise.reject("permission", "The location permission is needed to see Wi-Fi names", e)
    } catch (e: Exception) {
      promise.reject("scan_failed", e.message, e)
    }
  }

  private fun join(ssid: String, password: String, promise: Promise) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
      promise.reject("unsupported", "Joining from the app needs Android 10 or newer", null)
      return
    }
    try {
      release()
      val spec = WifiNetworkSpecifier.Builder().setSsid(ssid)
      if (password.isNotEmpty()) spec.setWpa2Passphrase(password)
      val request = NetworkRequest.Builder()
        .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
        .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        .setNetworkSpecifier(spec.build())
        .build()
      var answered = false
      val cb = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
          connectivity.bindProcessToNetwork(network)
          if (!answered) {
            answered = true
            promise.resolve(true)
          }
        }

        override fun onUnavailable() {
          // the person dismissed Android's sheet, or the hotspot was not found in time
          if (!answered) {
            answered = true
            promise.reject("unavailable", "The hotspot was not joined", null)
          }
          release()
        }

        override fun onLost(network: Network) {
          connectivity.bindProcessToNetwork(null)
        }
      }
      callback = cb
      connectivity.requestNetwork(request, cb, 60000)
    } catch (e: Exception) {
      promise.reject("join_failed", e.message, e)
    }
  }

  /**
   * ANVE boxes on the Wi-Fi the phone is on. Every set-up box announces itself as the
   * service "_anve._tcp" (with its short id), so nobody has to type an address.
   */
  private fun discover(timeoutMs: Int, promise: Promise) {
    try {
      val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
      val found = Collections.synchronizedList(ArrayList<Map<String, Any>>())
      val listener = object : NsdManager.DiscoveryListener {
        override fun onServiceFound(info: NsdServiceInfo) {
          try {
            @Suppress("DEPRECATION")
            nsd.resolveService(
              info,
              object : NsdManager.ResolveListener {
                override fun onResolveFailed(i: NsdServiceInfo, code: Int) {}
                override fun onServiceResolved(i: NsdServiceInfo) {
                  @Suppress("DEPRECATION")
                  // IPv4 only: an IPv6 link-local address ("fe80::...%wlan0") cannot be put
                  // in the app's http:// address as it is (seen on Android 16, 18 Nov 2026)
                  val host = if (Build.VERSION.SDK_INT >= 34) {
                    i.hostAddresses.firstOrNull { it is Inet4Address }
                  } else {
                    i.host?.takeIf { it is Inet4Address }
                  }
                  val address = host?.hostAddress ?: return
                  val id = i.attributes["id"]?.let { String(it) } ?: ""
                  found.add(mapOf("name" to (i.serviceName ?: ""), "address" to address, "port" to i.port, "id" to id))
                }
              }
            )
          } catch (e: Exception) {}
        }

        override fun onServiceLost(info: NsdServiceInfo) {}
        override fun onDiscoveryStarted(type: String) {}
        override fun onDiscoveryStopped(type: String) {}
        override fun onStartDiscoveryFailed(type: String, code: Int) {}
        override fun onStopDiscoveryFailed(type: String, code: Int) {}
      }
      nsd.discoverServices("_anve._tcp", NsdManager.PROTOCOL_DNS_SD, listener)
      main.postDelayed({
        try { nsd.stopServiceDiscovery(listener) } catch (e: Exception) {}
        promise.resolve(found.toList().distinctBy { it["address"] })
      }, timeoutMs.coerceIn(500, 15000).toLong())
    } catch (e: Exception) {
      promise.resolve(emptyList<Map<String, Any>>())
    }
  }

  private fun release() {
    try { connectivity.bindProcessToNetwork(null) } catch (e: Exception) {}
    callback?.let {
      try { connectivity.unregisterNetworkCallback(it) } catch (e: Exception) {}
    }
    callback = null
  }
}
