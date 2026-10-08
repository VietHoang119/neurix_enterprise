import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import type { User } from "firebase/auth";
import "./global.css";
import { onAuthTokenChanged } from "./lib/firebase";
import { TooltipProvider } from "./components/ui/tooltip";
import Login from "./pages/Login";
import Shell from "./pages/Shell";

const App = () => {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => onAuthTokenChanged(setUser), []);

  if (user === undefined) {
    return null; // resolving initial auth state
  }

  return <TooltipProvider delayDuration={200}>{user ? <Shell user={user} /> : <Login />}</TooltipProvider>;
};

createRoot(document.getElementById("root")!).render(<App />);
