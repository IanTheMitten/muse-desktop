import { useState } from "react";
import { Link } from "react-router-dom";

import { LyricsEditDialog } from "../components/LyricsEditDialog";
import { UNTAGGED_NOTE } from "../player/radio";

import { CoverArt } from "../components/CoverArt";
import { TrackRow } from "../components/TrackRow";
import { unplayableReason } from "../player/formats";
import { FeelPicker, useFeel } from "../muse/FeelPicker";
import { MakePlaylist } from "../muse/MakePlaylist";
import { usePlayer } from "../player/PlayerContext";
import "../styles/muse.css";

export function Muse() {
  const {
    queue, index, current, mood, setMood, radioNote, retryMuse, playingFrom, isMusePick, playQueue,
    radioLift, setRadioLift, radioReason, energyReady, radioSeed,
  } = usePlayer();
  const seedTrack = queue.find((t) => t.track_rowid === radioSeed) ?? current;
  const [editing, setEditing] = useState(false);
  const feel = useFeel("muse.feel", mood, setMood);
  const upNext = queue.slice(index + 1);

  return (
    <div className="view">
      <h1 className="page-title">Muse</h1>
      <p className="lede">Tell Muse how you feel. It picks what plays next to match, starting from what's on now.</p>
      <div className="muse-grid">
        <section className="panel-card muse-steer" aria-labelledby="feelH">
          <h2 id="feelH">How are you feeling?</h2>
          <FeelPicker feel={feel} id="muse" />
          <div className="lift">
            <span id="lift-l" className="lift-name">Lift</span>
            <button
              type="button"
              role="switch"
              className="switch"
              aria-checked={radioLift}
              aria-labelledby="lift-l"
              aria-describedby="lift-d"
              onClick={() => setRadioLift(!radioLift)}
            >
              <span className="thumb" aria-hidden="true" />
              <span className="switch-state">{radioLift ? "On" : "Off"}</span>
            </button>
            <p id="lift-d" className="note">Radio drifts from heavier songs toward lighter ones over about 30 songs.</p>
            {!energyReady && (
              <p className="note">
                Energy needs smart features, so Muse matches only how dark or bright a mood is.{" "}
                <Link to="/settings">Settings</Link>
              </p>
            )}
          </div>
          {current && (
            <div className="from">
              <CoverArt src={`/api/tracks/${current.track_rowid}/art`} hasArt={current.has_art} size="thumb" />
              <div style={{ minWidth: 0 }}>
                <strong>{current.track_name}</strong>
                <span>Starting from {current.artist_name}</span>
              </div>
              <button type="button" className="btn" onClick={feel.clear}>Clear</button>
            </div>
          )}
        </section>

        <div className="muse-col">
          <section className="panel-card" aria-labelledby="nextH">
            <div className="section-head"><h2 id="nextH">Up next</h2></div>
            {!current ? (
              <p className="note">
                Play something and Muse follows it. <Link to="/library">Open the library</Link>
              </p>
            ) : (
              <>
                {radioNote?.failed && (
                  <p className="note">
                    {radioNote.text} <button type="button" className="link-btn" onClick={retryMuse}>Retry</button>
                  </p>
                )}
                {radioReason === "untagged" && (
                  <p className="note">
                    {UNTAGGED_NOTE}
                    {seedTrack && !seedTrack.has_lyrics && (
                      <>
                        {" "}
                        <button type="button" className="link-btn" onClick={() => setEditing(true)}>Add lyrics</button>
                      </>
                    )}
                  </p>
                )}
                <ol className="tracks">
                  {upNext.map((t, i) => (
                    <TrackRow
                      key={t.track_rowid}
                      variant="queue"
                      n={i + 1}
                      title={t.track_name}
                      sub={isMusePick(t.track_rowid) ? `${t.artist_name} · Muse` : t.artist_name}
                      durationMs={t.duration_ms}
                      artSrc={t.has_art ? `/api/tracks/${t.track_rowid}/art` : null}
                      unplayable={unplayableReason(t)}
                      onPlay={() => playQueue(queue, index + 1 + i, playingFrom ?? undefined)}
                    />
                  ))}
                </ol>
              </>
            )}
          </section>

          <section className="panel-card" aria-labelledby="makeH">
            <h2 id="makeH">Make a playlist</h2>
            <p className="note">Built from how you feel right now.</p>
            <MakePlaylist feel={feel} seed={current?.track_rowid ?? null} />
          </section>
        </div>
      </div>
      {editing && seedTrack && (
        <LyricsEditDialog track={seedTrack} onClose={() => setEditing(false)} onSaved={() => {}} />
      )}
    </div>
  );
}
