import { withBasePath } from '../utils/basePath';

export const Mascot = () => (
  <img
    src={withBasePath('/mascot.png')}
    alt=""
    aria-hidden="true"
    className="pointer-events-none fixed right-4 bottom-4 z-30 hidden h-28 w-28 object-contain opacity-90 sm:h-36 sm:w-36 lg:block"
    style={{
      filter: 'drop-shadow(0 4px 12px rgb(0 0 0 / 0.25))',
      animation: 'mascot-bob 4s ease-in-out infinite',
    }}
  />
);
