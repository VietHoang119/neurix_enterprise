import { useState } from "react";
import { signInWithGoogle } from "../lib/firebase";

export default function Login() {
  const [error, setError] = useState<string | null>(null);
  const [isSigningIn, setIsSigningIn] = useState(false);

  const handleSignIn = async () => {
    setError(null);
    setIsSigningIn(true);
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setIsSigningIn(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center", maxWidth: 360 }}>
        <h1>Neurix Enterprise</h1>
        <p>Sign in with your organization's Google account to continue.</p>
        <button onClick={handleSignIn} disabled={isSigningIn}>
          {isSigningIn ? "Signing in..." : "Sign in with Google"}
        </button>
        {error && <p style={{ color: "crimson" }}>{error}</p>}
      </div>
    </div>
  );
}
