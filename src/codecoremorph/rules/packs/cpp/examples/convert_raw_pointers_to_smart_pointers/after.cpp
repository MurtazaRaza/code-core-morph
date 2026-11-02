class Scene {
public:
    Scene() : root_(std::make_unique<Node>("root")) {}
    void add(std::unique_ptr<Mesh> mesh) { meshes_.push_back(std::move(mesh)); }
    Node* root() const { return root_.get(); }  // still a non-owning view

private:
    std::unique_ptr<Node> root_;
    std::vector<std::unique_ptr<Mesh>> meshes_;
};
