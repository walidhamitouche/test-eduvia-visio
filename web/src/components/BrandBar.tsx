import { useState } from "react";

import type { Brand } from "../brand";

/** Logo et nom de la marque. Un logo qui ne charge pas disparaît : le nom reste. */
export function BrandBar({ brand }: { brand: Brand }) {
  const [logoFailed, setLogoFailed] = useState(false);
  return (
    <div className="brand-bar">
      {brand.logoUrl && !logoFailed ? (
        <img
          className="brand-logo"
          src={brand.logoUrl}
          alt=""
          referrerPolicy="no-referrer"
          onError={() => setLogoFailed(true)}
        />
      ) : null}
      <span className="brand-name">{brand.name}</span>
    </div>
  );
}
