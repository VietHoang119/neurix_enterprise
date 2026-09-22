import { initializeApp } from "firebase/app";
import {
  GoogleAuthProvider,
  getAuth,
  onIdTokenChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
  type User,
} from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();

export const signInWithGoogle = () => signInWithPopup(auth, googleProvider);
export const signOut = () => firebaseSignOut(auth);

/**
 * Current ID token. The Firebase SDK rotates the token ~10 min before
 * expiry in the background; this just reads whatever it currently holds,
 * forcing a refresh only if the caller asks for one.
 */
export const getIdToken = (forceRefresh = false): Promise<string | null> => {
  const user = auth.currentUser;
  if (!user) return Promise.resolve(null);
  return user.getIdToken(forceRefresh);
};

/**
 * Fires on sign-in, sign-out, and every background token rotation — this is
 * what client.ts and App.tsx key off of, replacing neurix-ui's
 * localStorage-based authStorage.
 */
export const onAuthTokenChanged = (callback: (user: User | null) => void) =>
  onIdTokenChanged(auth, callback);
