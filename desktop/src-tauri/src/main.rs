fn main() {
    if let Err(error) = dumbmoney_desktop_lib::run() {
        eprintln!("{error}");
        let diagnostic_path = std::env::temp_dir().join("dumbmoney-desktop-startup-error.log");
        let _ = std::fs::write(diagnostic_path, format!("{error}\n"));
        std::process::exit(1);
    }
}
