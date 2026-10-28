import Image from "next/image";

export function BrandLogo() {
  return (
    <Image
      className="brand-logo"
      src="/assets/yuliang-logo.png"
      alt="易致·驭量，自主投放，让每一份增长都有据可依"
      width={1926}
      height={445}
      unoptimized
      priority
    />
  );
}

export function BrandMark() {
  return <span className="brand-mark" role="img" aria-label="易致·驭量" />;
}
