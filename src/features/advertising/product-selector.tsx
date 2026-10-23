"use client";
import {
  advertisingProducts,
  advertisingProviderNames,
  type AdvertisingProduct,
} from "@/lib/advertising";
export function ProductScopeSelector({
  value,
  onChange,
  allowAll = true,
}: {
  value: AdvertisingProduct | "all";
  onChange: (value: AdvertisingProduct | "all") => void;
  allowAll?: boolean;
}) {
  const provider =
    value === "all" ? "all" : advertisingProducts[value].provider;
  return (
    <>
      <label>
        投放平台
        <select
          aria-label="投放平台"
          value={provider}
          onChange={(e) => {
            const p = e.target.value;
            if (p === "all") onChange("all");
            else {
              const first = Object.entries(advertisingProducts).find(
                ([, v]) => v.provider === p,
              );
              if (first) onChange(first[0] as AdvertisingProduct);
            }
          }}
        >
          {allowAll && <option value="all">全平台 · 经营汇总</option>}
          {Object.entries(advertisingProviderNames).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        产品线
        <select
          aria-label="投放产品线"
          value={value}
          disabled={provider === "all"}
          onChange={(e) => onChange(e.target.value as AdvertisingProduct)}
        >
          {provider === "all" ? (
            <option value="all">汇总模式</option>
          ) : (
            Object.entries(advertisingProducts)
              .filter(([, p]) => p.provider === provider)
              .map(([id, p]) => (
                <option key={id} value={id}>
                  {p.name}
                  {!p.writeEnabled ? " · 创建待核验" : ""}
                </option>
              ))
          )}
        </select>
      </label>
    </>
  );
}
