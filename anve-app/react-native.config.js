// Two libraries are used on iPhone only: the standard Lottie player and the standard
// "safe area" library. On Android the app has its own small replacements for both
// (modules/anve-lottie, modules/anve-wifi), because these two add a C++ build step whose
// file paths are too long for the Windows build (see build-android.ps1). Leaving them out
// of the Android build here keeps that build exactly as it was.
module.exports = {
  dependencies: {
    'lottie-react-native': { platforms: { android: null } },
    'react-native-safe-area-context': { platforms: { android: null } },
  },
};
