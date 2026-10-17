import SwiftUI

/// Shared-catalog form. Activation waits for versioned persistence and server capability checks.
struct MedicalWoundProfileFields: View {
    @Binding var profile: MedicalWoundProfile
    @Binding var specialNote: String
    @State private var region = ""
    private let catalog = MedicalWoundCatalog.shared

    private func optionPicker(_ title: String, selection: Binding<String>, codes: [String], options: [MedicalWoundCatalog.Option]) -> some View {
        Picker(title, selection: selection) {
            Text("請選擇").tag("")
            ForEach(codes, id: \.self) { code in
                Text(catalog.label(code, in: options)).tag(code)
            }
        }
    }
    var body: some View {
        Group {
            Picker("身體區域", selection: $region) {
                Text("請選擇").tag("")
                ForEach(catalog.regions) { Text($0.label).tag($0.code) }
            }.onChange(of: region) { _, value in
                if catalog.sites.first(where: { $0.code == profile.site })?.region != value {
                    profile.site = ""; profile.side = ""; profile.surface = ""; profile.digit = "not_applicable"
                }
            }
            Picker("詳細部位", selection: $profile.site) {
                Text("請選擇").tag("")
                ForEach(catalog.sites.filter { $0.region == region }) { Text($0.label).tag($0.code) }
            }.disabled(region.isEmpty)
                .onChange(of: profile.site) { _, value in
                    guard let site = catalog.sites.first(where: { $0.code == value }) else { return }
                    // Changing location must not carry an incompatible surface or digit forward.
                    if !site.lateralities.contains(profile.side) { profile.side = "" }
                    if !site.surfaces.contains(profile.surface) { profile.surface = site.surfaces.count == 1 ? site.surfaces[0] : "" }
                    if !site.digits.contains(profile.digit) { profile.digit = site.digits.count == 1 ? site.digits[0] : "" }
                }
            if let site = catalog.sites.first(where: { $0.code == profile.site }) {
                optionPicker("左右側", selection: $profile.side, codes: site.lateralities, options: catalog.lateralities)
                if site.surfaces.count > 1 {
                    optionPicker("表面", selection: $profile.surface, codes: site.surfaces, options: catalog.surfaces)
                }
                if site.digits.count > 1 {
                    Picker("指／趾序號", selection: $profile.digit) {
                        Text("請選擇").tag("")
                        ForEach(site.digits, id: \.self) { code in
                            Text(code == "unknown" ? "尚未判定" : "第 \(code) 指／趾").tag(code)
                        }
                    }
                }
            }
            Picker("主要傷口成因（臨床確認）", selection: $profile.etiology) {
                Text("請選擇").tag("")
                ForEach(catalog.etiologies) { Text($0.label).tag($0.code) }
            }
            DisclosureGroup("共存因素") {
                ForEach(catalog.factors) { option in
                    Toggle(option.label, isOn: Binding(get: { profile.factors.contains(option.code) }, set: { selected in
                        profile.factors.removeAll { $0 == option.code }
                        if selected { profile.factors.append(option.code); profile.factors.sort() }
                    }))
                }
            }
            if profile.site == "other" || profile.etiology == "other" {
                TextField("請補充其他部位／成因", text: $specialNote, axis: .vertical)
                Text("其他選項須補充說明；不會用這段文字自動計分。").font(.caption)
            } else {
                DisclosureGroup("特殊案件補充（選填）") {
                    TextField("特殊情況說明", text: $specialNote, axis: .vertical)
                }
            }
            ForEach(catalog.guidance(etiology: profile.etiology)) { instrument in
                VStack(alignment: .leading, spacing: 4) {
                    Text(instrument.label).font(.headline)
                    Text("可協助整理：" + instrument.automatic)
                    Text("臨床待確認：" + instrument.manual)
                    Text("目前僅顯示評估需求，尚未啟用此量表計分。").font(.caption).foregroundStyle(.secondary)
                }
            }
            if !profile.site.isEmpty && !profile.etiology.isEmpty && !profile.valid {
                Text("請完成並核對部位、左右側、表面及成因組合。").foregroundStyle(.orange)
            }
        }.onAppear { region = catalog.sites.first(where: { $0.code == profile.site })?.region ?? "" }
    }
}
