import { Link } from "react-router";

import { PlayIcon } from "./Icons";
import { CoverArt } from "./CoverArt";

/* One link (art + names); the play button sits beside it, over the art's corner. */
export function CoverCard(props: {
  to: string;
  title: string;
  subtitle: string;
  artSrc: string | null;
  onPlay?: () => void;
}) {
  const { to, title, subtitle, artSrc, onPlay } = props;
  return (
    <div className="cover">
      <Link className="cover-link" to={to}>
        <span className="cover-img">
          <CoverArt src={artSrc} hasArt={artSrc !== null} size="tile" />
        </span>
        <span className="cover-t">{title}</span>
        <span className="cover-a">{subtitle}</span>
      </Link>
      {onPlay && (
        <span className="cover-zone">
          <button type="button" className="cover-play" aria-label={`Play ${title}`} onClick={onPlay}>
            <PlayIcon aria-hidden="true" />
          </button>
        </span>
      )}
    </div>
  );
}
