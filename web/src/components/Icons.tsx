/* One icon set, drawn here.
 *
 * The transport used to be emoji — 👎 ⏮ ▶ ⏭ — which is a different typeface on
 * every platform, sits on a different baseline, and cannot take the stroke
 * weight the rest of the chrome uses. These are on the same 24 grid at the same
 * 1.6 stroke as the nav glyphs, so the bar and the tabs read as one system.
 *
 * Drawn rather than imported: a dozen paths is not a dependency, and a font or
 * icon package is one more thing to fetch over a tailnet that may not reach it.
 */

type Props = { className?: string };

function Icon({ className = "icon", children }: Props & { children: React.ReactNode }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      {children}
    </svg>
  );
}

export const PlayIcon = (props: Props) => (
  <Icon {...props}>
    <path className="icon-fill" d="M8 5.5 19 12 8 18.5z" />
  </Icon>
);

export const PauseIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M9 5.5v13M15 5.5v13" />
  </Icon>
);

export const PrevIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M18.5 6v12L10 12zM6 6v12" />
  </Icon>
);

export const NextIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M5.5 6v12L14 12zM18 6v12" />
  </Icon>
);

export const GripIcon = (props: Props) => (
  <Icon {...props}>
    <circle className="icon-fill" cx="9" cy="6.5" r="1.4" />
    <circle className="icon-fill" cx="9" cy="12" r="1.4" />
    <circle className="icon-fill" cx="9" cy="17.5" r="1.4" />
    <circle className="icon-fill" cx="15" cy="6.5" r="1.4" />
    <circle className="icon-fill" cx="15" cy="12" r="1.4" />
    <circle className="icon-fill" cx="15" cy="17.5" r="1.4" />
  </Icon>
);

export const CloseIcon = (props: Props) => (
  <Icon {...props}>
    <path d="m6.5 6.5 11 11M17.5 6.5l-11 11" />
  </Icon>
);

export const QueueIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M4 7h11M4 12h11M4 17h7" />
    <path d="M18 10v8" />
    <circle className="icon-fill" cx="19.5" cy="17.5" r="2" />
  </Icon>
);

export const ShuffleIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M4 7h3.5l9 10H20M16.5 7H20M4 17h3.5l2.2-2.4" />
    <path d="m17.5 4.5 2.5 2.5-2.5 2.5M17.5 14.5 20 17l-2.5 2.5" />
  </Icon>
);

export const RepeatIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M7.5 7h9a3.5 3.5 0 0 1 0 7h-9a3.5 3.5 0 0 1 0-7" />
    <path d="m9.5 4.5-2.5 2.5 2.5 2.5M14.5 11.5l2.5 2.5-2.5 2.5" />
  </Icon>
);

export const ThumbDownIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M14.5 4.5v9l-3.2 5.4a2 2 0 0 1-3-2.4l.9-3h-4a2 2 0 0 1-2-2.4l1.1-5a2 2 0 0 1 2-1.6z" />
    <path d="M17 4.5h3v9h-3" />
  </Icon>
);

/** A sheet collapses downward. A left chevron would promise a back stack. */
export const CollapseIcon = (props: Props) => (
  <Icon {...props}>
    <path d="m6 9 6 6 6-6" />
  </Icon>
);

export const ExpandIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M9 4.5H4.5V9M15 4.5h4.5V9M9 19.5H4.5V15M15 19.5h4.5V15" />
  </Icon>
);

export const ChevronIcon = (props: Props) => (
  <Icon {...props}>
    <path d="m9 5 7 7-7 7" />
  </Icon>
);

export const BackIcon = (props: Props) => (
  <Icon {...props}>
    <path d="m15 5-7 7 7 7" />
  </Icon>
);

export const DownloadIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14" />
  </Icon>
);

export const RetryIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.6-5.7" />
    <path d="M19.5 4.5V9H15" />
  </Icon>
);

export const ErrorIcon = (props: Props) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7.5v5M12 16h.01" />
  </Icon>
);

export const SearchGlyph = (props: Props) => (
  <Icon {...props}>
    <circle cx="11" cy="11" r="6.25" />
    <path d="M15.6 15.6 20 20" />
  </Icon>
);

export const LinkIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l3-3a3.54 3.54 0 0 0-5-5l-1.5 1.5" />
    <path d="M13.5 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.54 3.54 0 0 0 5 5l1.5-1.5" />
  </Icon>
);

export const CheckIcon = (props: Props) => (
  <Icon {...props}>
    <path d="m5 12.5 4.5 4.5L19 7" />
  </Icon>
);

export const MuseIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M12 3.5c.6 4.4 2.4 6.6 6.5 7.3v.4c-4.1.7-5.9 2.9-6.5 7.3h-.4c-.6-4.4-2.4-6.6-6.5-7.3v-.4c4.1-.7 5.9-2.9 6.5-7.3z" />
    <path d="M19 3.5v3M17.5 5h3" />
  </Icon>
);

export const PlaylistsIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M4 7h16M4 12h16M4 17h9" />
  </Icon>
);

export const LyricsIcon = (props: Props) => (
  <Icon {...props}>
    <path d="M5 5h14v10H9l-4 4z" />
    <path d="M9 9h6M9 12h4" />
  </Icon>
);

export const HomeGlyph = (props: Props) => (
  <Icon {...props}>
    <path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z" />
  </Icon>
);

export const SettingsGlyph = (props: Props) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" />
  </Icon>
);

export const LibraryGlyph = (props: Props) => (
  <Icon {...props}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="2" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="2" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="2" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="2" />
  </Icon>
);

export const AddGlyph = (props: Props) => (
  <Icon {...props}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
