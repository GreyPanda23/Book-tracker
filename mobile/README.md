# Book Tracker – iPhone app (Expo Go)

A thin wrapper: this app opens the deployed Streamlit site full-screen in a WebView.
All features and data live in the Streamlit app; change the address in `config.js` if it moves.

## Run it
1. Install Node.js (LTS) from nodejs.org and Expo Go from the App Store.
2. In Terminal:
   ```
   cd mobile
   npm install
   npx expo install --fix     # picks the package versions that match Expo Go
   npx expo start
   ```
3. Scan the QR code with the iPhone camera. Same Wi-Fi needed; otherwise use `npx expo start --tunnel`.

If Expo Go says the project is incompatible with its SDK version, run `npx expo install --fix` again,
then `npx expo start --clear`.

Streamlit's free tier sleeps when idle: after a quiet spell the first open shows
"Your app is asleep" - tap *Yes, get this app back up!* and wait a minute.
