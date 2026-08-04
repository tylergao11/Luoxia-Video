"use client";

import { useState, useEffect } from "react";
import { useSettingsStore, type ThemePreset } from "@/store/settingsStore";

interface LuoxiaBrandingProps {
  size?: "sm" | "md";
  showSlogan?: boolean;
}

// Logo variants by theme (circuit maple leaf, transparent bg).
const LOGO_SRC: Record<ThemePreset, string> = {
  "atelier-dark": "/logo-dark.png",
  "bridge-dark": "/logo-dark.png",
  "brand-dark": "/logo-dark.png",
  "atelier-light": "/logo-light-teal.png",
  "brand-light": "/logo-light.png",
};
const ATELIER_DARK_FILTER = "hue-rotate(-64deg) saturate(1.35) brightness(1.08)";

export default function LuoxiaBranding({ size = "md", showSlogan = true }: LuoxiaBrandingProps) {
  const logoSize = size === "sm" ? "w-9 h-9" : "w-14 h-14";
  const titleSize = size === "sm" ? "text-lg" : "text-xl";

  const theme = useSettingsStore((s) => s.theme);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const activeTheme: ThemePreset = mounted ? theme : "atelier-dark";
  const logoSrc = LOGO_SRC[activeTheme] ?? "/logo-dark.png";
  const logoFilter = activeTheme === "atelier-dark" ? ATELIER_DARK_FILTER : undefined;

  return (
    <div>
      <div className="flex gap-3 items-center">
        <div className="flex-shrink-0">
          <img
            src={logoSrc}
            alt="Luoxia-Video"
            className={`${logoSize} object-contain`}
            style={logoFilter ? { filter: logoFilter } : undefined}
          />
        </div>
        <div className="flex flex-col justify-center">
          <div className="flex items-baseline gap-0">
            <span className={`font-mono ${titleSize} font-bold tracking-tight text-foreground`}>
              LUOXIA
            </span>
            <span className={`font-mono ${titleSize} font-black tracking-tight text-primary`}>
              ·V
            </span>
          </div>
          {size !== "sm" && (
            <span className="font-mono text-[0.6875rem] text-text-muted tracking-[0.2em] uppercase -mt-0.5">
              落霞
            </span>
          )}
        </div>
      </div>
      {showSlogan && (
        <p className="font-mono atelier-display text-[0.5rem] text-text-muted tracking-[0.15em] text-center mt-2.5 uppercase">
          Novel to Short Drama
        </p>
      )}
    </div>
  );
}

