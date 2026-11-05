- **NixOS works now:** the Nix package crashed on launch with `No module named 'gi'`. Fixed and
  tested on a live NixOS: `nix run github:lamex30/yumi`. The flake also pins nixpkgs now.
- **Do Not Disturb on KDE Plasma:** the button used to change only her sprite. Now it really
  silences notifications, and Plasma lifts it by itself if she quits or crashes.
- **She dances to the beat:** the bass from `cava` drives her hop while music plays.
- **Restart button** in the settings.
- **Waking from a nap:** a click, the menu or the hotkey now wakes her from an idle nap.
- **Fewer side effects:** she no longer steals the keyboard from the compositor, cleans up her
  child processes, and the tools she starts no longer inherit her `LD_PRELOAD`.
